import datetime
import json
import logging
import logging.handlers
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
from collections import OrderedDict, deque
from pathlib import Path

import darkdetect
import exifread
import numpy as np
import rawpy
from PyQt5.QtCore import QAbstractTableModel, QCoreApplication, QEvent, QItemSelectionModel, QModelIndex, QPoint, QRect, QSize, Qt, QThread, QTimer, QStandardPaths, pyqtSignal
from PyQt5.QtGui import (
    QColor,
    QIcon,
    QImage,
    QImageReader,
    QPalette,
    QPen,
    QPixmap,
    QPolygon,
)
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListView,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QSpinBox,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

try:
    from win32mica import ApplyMica, MicaStyle, MicaTheme
except ImportError:  # pragma: no cover - optional dependency on some systems
    ApplyMica = None
    MicaStyle = None
    MicaTheme = None


APP_NAME = "Image Picker"
APP_ID = "ImagePicker"
APP_VERSION = "0.35"
APP_DISPLAY_NAME = f"{APP_NAME} - v{APP_VERSION}"
RAW_EXTENSIONS = {".arw", ".raw", ".cr2", ".nef", ".dng"}
JPEG_EXTENSIONS = {".jpg", ".jpeg"}
IMAGE_EXTENSIONS = JPEG_EXTENSIONS | RAW_EXTENSIONS | {".png", ".tif", ".tiff", ".bmp", ".webp", ".heic"}
MAX_EXIF_CACHE = 4096
MAX_RAW_CACHE = 16


def ensure_application_identity():
    """Set the Qt application identity before anything reads QStandardPaths.

    Both the log file and the settings file derive their location from this, so
    it has to be configured at import time rather than in main().
    """
    QCoreApplication.setApplicationName(APP_ID)
    QCoreApplication.setOrganizationName(APP_ID)


def is_windows():
    return platform.system() == "Windows"


def safe_is_dark():
    try:
        return bool(darkdetect.isDark())
    except Exception:
        return True


def theme_is_dark(setting_value):
    if setting_value is None:
        return safe_is_dark()
    return bool(setting_value)


def get_app_config_dir():
    ensure_application_identity()
    base_dir = QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation)
    if not base_dir:
        base_path = Path.home() / ".image_picker" / APP_ID
    else:
        base_path = Path(base_dir)
        if base_path.name.casefold() != APP_ID.casefold():
            base_path = base_path / APP_ID
    try:
        base_path.mkdir(parents=True, exist_ok=True)
    except OSError:
        base_path = Path.home() / ".image_picker" / APP_ID
        base_path.mkdir(parents=True, exist_ok=True)
    return base_path


def setup_logging():
    log_dir = get_app_config_dir()
    log_file = log_dir / "image_picker.log"
    logger = logging.getLogger("image_picker")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    has_file_handler = any(
        isinstance(handler, logging.handlers.RotatingFileHandler)
        and Path(handler.baseFilename) == log_file
        for handler in logger.handlers
    )
    if not has_file_handler:
        try:
            file_handler = logging.handlers.RotatingFileHandler(
                log_file, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError:
            pass

    has_stream_handler = any(
        isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
        for handler in logger.handlers
    )
    if not has_stream_handler:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    return logger


ensure_application_identity()
LOGGER = setup_logging()


def safe_apply_mica(widget, is_dark):
    if not is_windows() or ApplyMica is None:
        return

    try:
        hwnd = int(widget.winId())
        theme = MicaTheme.DARK if is_dark else MicaTheme.LIGHT
        ApplyMica(hwnd, Theme=theme, Style=MicaStyle.DEFAULT)
        widget.setAttribute(Qt.WA_TranslucentBackground)
    except Exception:
        LOGGER.exception("Failed to apply Mica")


def qss_icon_url(file_name):
    return ((Path(__file__).resolve().parent / "icons" / file_name).as_posix())


def trim_cache(cache, max_items):
    while len(cache) > max_items:
        cache.popitem(last=False)


def normalize_path(value):
    try:
        return os.path.normcase(os.path.abspath(str(value)))
    except (TypeError, ValueError):
        return str(value)


def paths_are_same(first_path, second_path):
    if not first_path or not second_path:
        return False
    return normalize_path(first_path) == normalize_path(second_path)


def is_raw_file(file_path):
    return Path(file_path).suffix.lower() in RAW_EXTENSIONS


def is_pairable_jpeg(file_path):
    return Path(file_path).suffix.lower() in JPEG_EXTENSIONS


def parse_exif_datetime(datetime_str):
    if not datetime_str:
        return None

    try:
        return datetime.datetime.strptime(str(datetime_str), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def parse_exposure_seconds(tag_value):
    """Convert an EXIF exposure value such as "1/250" or "2" to seconds."""
    text = exif_text(tag_value)
    if not text:
        return None
    if "/" in text:
        try:
            num, den = text.split("/", 1)
            denominator = float(den)
            if denominator == 0:
                return None
            return float(num) / denominator
        except ValueError:
            return None
    try:
        return float(text.rstrip("s"))
    except ValueError:
        return None


def exif_text(tag_value):
    if tag_value is None:
        return ""
    return str(tag_value).strip()


def natural_sort_key(value):
    parts = re.split(r"(\d+)", str(value).casefold())
    return [int(part) if part.isdigit() else part for part in parts]


def parse_leading_number(value):
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def format_f_number(tag_value):
    text = exif_text(tag_value)
    if not text:
        return ""
    if "/" in text:
        try:
            num, den = text.split("/", 1)
            return f"f/{float(num) / float(den):.1f}"
        except Exception:
            return text
    return f"f/{text}"


def format_focal_length(tag_value):
    text = exif_text(tag_value)
    if not text:
        return ""
    if "/" in text:
        try:
            num, den = text.split("/", 1)
            return f"{float(num) / float(den):.0f}mm"
        except Exception:
            return text
    if text.endswith("mm"):
        return text
    return f"{text}mm"


def extract_exif(file_path):
    defaults = {
        "date": "",
        "time": "",
        "f_stop": "",
        "exposure": "",
        "exposure_seconds": None,
        "iso": "",
        "focal_length": "",
        "lens_model": "",
        "datetime_original": "",
    }
    try:
        with open(file_path, "rb") as file_handle:
            tags = exifread.process_file(file_handle, details=False)

        datetime_text = exif_text(tags.get("EXIF DateTimeOriginal") or tags.get("Image DateTime"))
        dt_obj = parse_exif_datetime(datetime_text)

        data = defaults.copy()
        data["datetime_original"] = datetime_text
        if dt_obj:
            data["date"] = dt_obj.strftime("%d-%m-%y")
            data["time"] = dt_obj.strftime("%H:%M:%S")

        data["f_stop"] = format_f_number(tags.get("EXIF FNumber"))
        exposure_tag = tags.get("EXIF ExposureTime")
        data["exposure"] = exif_text(exposure_tag)
        data["exposure_seconds"] = parse_exposure_seconds(exposure_tag)
        data["iso"] = exif_text(tags.get("EXIF ISOSpeedRatings"))
        data["focal_length"] = format_focal_length(tags.get("EXIF FocalLength"))
        data["lens_model"] = exif_text(tags.get("EXIF LensModel"))
        return data
    except Exception:
        LOGGER.warning("Could not read EXIF data from %s", file_path, exc_info=True)
        return defaults.copy()


def exif_filename_from_metadata(exif_data, fallback_stem, rename_format):
    raw_text = exif_data.get("datetime_original", "")
    dt_obj = parse_exif_datetime(raw_text)
    if not dt_obj:
        return fallback_stem

    format_map = {
        "YYYYMMDD-HHMMSS": "%Y%m%d-%H%M%S",
        "YYYYMMDD-HHMM": "%Y%m%d-%H%M",
    }
    fmt = format_map.get(rename_format, "%Y%m%d-%H%M%S")
    return dt_obj.strftime(fmt)


def scaled_image_for_size(image, target_size, smooth=True):
    if image.isNull():
        return QImage()
    width = max(target_size.width(), 1)
    height = max(target_size.height(), 1)
    transform_mode = Qt.SmoothTransformation if smooth else Qt.FastTransformation
    return image.scaled(width, height, Qt.KeepAspectRatio, transform_mode)


def create_thumbnail_image(image_path, thumbnail_size):
    try:
        thumb_size = max(32, int(thumbnail_size))
        target = QSize(thumb_size, thumb_size)
        if is_raw_file(image_path):
            with rawpy.imread(image_path) as raw:
                try:
                    thumb = raw.extract_thumb()
                    if thumb.format == rawpy.ThumbFormat.JPEG:
                        image = QImage.fromData(thumb.data)
                    else:
                        thumb_array = np.array(thumb.data)
                        height, width, channels = thumb_array.shape
                        image = QImage(thumb_array.data, width, height, width * channels, QImage.Format_RGB888).copy()
                except Exception:
                    rgb = raw.postprocess(half_size=True, output_bps=8)
                    height, width, channels = rgb.shape
                    image = QImage(rgb.data, width, height, width * channels, QImage.Format_RGB888).copy()
                return scaled_image_for_size(image, target)

        reader = QImageReader(image_path)
        reader.setAutoTransform(True)
        source_size = reader.size()
        if source_size.isValid():
            source_size.scale(target, Qt.KeepAspectRatio)
            reader.setScaledSize(source_size)
        image = reader.read()
        if image.isNull():
            return QImage()
        return scaled_image_for_size(image, target)
    except Exception:
        LOGGER.warning("Could not create thumbnail for %s", image_path, exc_info=True)
        return QImage()


def iter_image_files(directory, include_subfolders, should_continue=None, progress_callback=None):
    """Yield file records while walking the folder.

    os.scandir keeps this cheap on large folders: each directory entry is
    stat'ed at most once, unreadable subfolders are skipped instead of aborting
    the whole scan, and symlinked folders are not followed.
    """
    directory_path = Path(directory)
    pending_dirs = [directory_path]
    scanned_count = 0

    while pending_dirs:
        current_dir = pending_dirs.pop()
        try:
            with os.scandir(current_dir) as scan_iterator:
                for entry in scan_iterator:
                    if should_continue is not None and not should_continue():
                        return
                    try:
                        if entry.is_dir():
                            if include_subfolders and not entry.is_symlink():
                                pending_dirs.append(Path(entry.path))
                            continue
                        if not entry.is_file():
                            continue
                    except OSError:
                        continue

                    path = Path(entry.path)
                    if path.suffix.lower() not in IMAGE_EXTENSIONS:
                        continue
                    try:
                        modified_time = entry.stat().st_mtime
                    except OSError:
                        modified_time = 0
                    try:
                        relative_path = path.relative_to(directory_path)
                    except ValueError:
                        relative_path = Path(path.name)

                    scanned_count += 1
                    if progress_callback is not None and scanned_count % 200 == 0:
                        progress_callback(scanned_count)

                    yield {
                        "full_path": str(path),
                        "display_name": str(relative_path).replace("\\", "/"),
                        "suffix": path.suffix.lower(),
                        "group_key": str(relative_path.with_suffix("")).replace("\\", "/").lower(),
                        "modified_time": modified_time,
                    }
        except OSError:
            LOGGER.warning("Could not scan folder %s", current_dir, exc_info=True)


def scan_image_files(directory, include_subfolders, should_continue=None):
    file_records = list(iter_image_files(directory, include_subfolders, should_continue))
    file_records.sort(key=lambda item: item["display_name"].lower())
    return file_records


def build_single_entry(record):
    path = record["full_path"]
    entry = {
        "entry_id": normalize_path(path),
        "pair_key": record["group_key"],
        "display_name": record["display_name"],
        "jpg_path": path if is_pairable_jpeg(path) else None,
        "raw_path": path if is_raw_file(path) else None,
        "single_path": None if (is_pairable_jpeg(path) or is_raw_file(path)) else path,
        "modified_time": record.get("modified_time", 0),
        "thumbnail": None,
        "exif": {},
    }
    entry["all_paths"] = [value for value in (entry["jpg_path"], entry["raw_path"], entry["single_path"]) if value]
    return entry


def build_grouped_entries(file_records):
    grouped = OrderedDict()
    for record in file_records:
        key = record["group_key"]
        entry = grouped.setdefault(
            key,
            {
                "entry_id": key,
                "pair_key": key,
                "display_name": record["display_name"],
                "jpg_path": None,
                "raw_path": None,
                "single_path": None,
                "modified_time": record.get("modified_time", 0),
                "thumbnail": None,
                "exif": {},
            },
        )
        entry["modified_time"] = max(entry.get("modified_time", 0), record.get("modified_time", 0))

        suffix = record["suffix"]
        if suffix in JPEG_EXTENSIONS:
            entry["jpg_path"] = record["full_path"]
            entry["display_name"] = record["display_name"]
        elif suffix in RAW_EXTENSIONS:
            entry["raw_path"] = record["full_path"]
            if not entry["jpg_path"]:
                entry["display_name"] = record["display_name"]
        else:
            entry["single_path"] = record["full_path"]
            entry["display_name"] = record["display_name"]

    entries = list(grouped.values())
    for entry in entries:
        entry["all_paths"] = [value for value in (entry["jpg_path"], entry["raw_path"], entry["single_path"]) if value]
    return entries


def choose_preview_path(entry, show_raw):
    if show_raw and entry.get("raw_path"):
        return entry["raw_path"]
    return entry.get("jpg_path") or entry.get("raw_path") or entry.get("single_path")


class SettingsManager:
    def __init__(self):
        legacy_path = Path(sys.argv[0]).resolve().with_name("settings.json")
        self.legacy_settings_file = legacy_path
        self.settings_dir = get_app_config_dir()
        self.settings_file = self.settings_dir / "settings.json"
        self.default_settings = {
            "default_source_folder": "",
            "destination_folder": "",
            "operation_mode": "copy",
            "dark_mode": None,
            "thumbnail_size": 64,
            "raw_preview_quality": "normal",
            "raw_gamma": 2.2,
            "auto_select_pairs": True,
            "rename_files": False,
            "rename_format": "YYYYMMDD-HHMMSS",
            "include_subfolders": False,
            "editor_path": "",
            "edit_directory": "",
            "save_edits_format": "jpg",
            "editor_presets": [],
        }
        self.current_settings = self.load_settings()

    def legacy_settings_files(self):
        candidates = [self.legacy_settings_file]
        generic_dir = QStandardPaths.writableLocation(QStandardPaths.GenericConfigLocation)
        if generic_dir:
            # Settings used to live in "<generic config>/Image Picker/ImagePicker"
            # because the app name contained a space when the directory was resolved.
            candidates.append(Path(generic_dir) / APP_NAME / APP_ID / "settings.json")
        seen = set()
        unique_candidates = []
        for candidate in candidates:
            key = normalize_path(candidate)
            if key in seen or key == normalize_path(self.settings_file):
                continue
            seen.add(key)
            unique_candidates.append(candidate)
        return unique_candidates

    def _read_json_file(self, file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as file_handle:
                data = json.load(file_handle)
                if isinstance(data, dict):
                    return data
        except Exception:
            LOGGER.warning("Could not load settings from %s", file_path, exc_info=True)
        return {}

    def _coerce_settings(self, raw_settings):
        settings = {}
        for key, default_value in self.default_settings.items():
            if key not in raw_settings:
                continue
            value = raw_settings[key]
            try:
                if default_value is None:
                    settings[key] = None if value is None else bool(value)
                elif isinstance(default_value, bool):
                    settings[key] = bool(value)
                elif isinstance(default_value, int):
                    settings[key] = int(value)
                elif isinstance(default_value, float):
                    settings[key] = float(value)
                elif isinstance(default_value, list):
                    settings[key] = list(value) if isinstance(value, (list, tuple)) else default_value
                else:
                    settings[key] = "" if value is None else str(value)
            except (TypeError, ValueError):
                LOGGER.warning("Ignoring invalid setting %r=%r", key, value)
        return settings

    def load_settings(self):
        settings = self.default_settings.copy()
        if self.settings_file.exists():
            settings.update(self._coerce_settings(self._read_json_file(self.settings_file)))
            return settings
        for legacy_file in self.legacy_settings_files():
            if legacy_file.exists():
                settings.update(self._coerce_settings(self._read_json_file(legacy_file)))
                break
        return settings

    def save_settings(self, settings):
        try:
            self.settings_dir.mkdir(parents=True, exist_ok=True)
            with open(self.settings_file, "w", encoding="utf-8") as file_handle:
                json.dump(settings, file_handle, indent=4)
            self.current_settings = settings.copy()
            return True
        except Exception:
            LOGGER.exception("Error saving settings to %s", self.settings_file)
            return False

    def get_setting(self, key):
        return self.current_settings.get(key, self.default_settings.get(key))


class SettingsWindow(QDialog):
    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.setWindowTitle("Settings")
        self.resize(520, 700)
        self.setMinimumSize(460, 420)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        scroll_area = QScrollArea(self)
        scroll_area.setObjectName("settingsScroll")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content_widget = QWidget()
        content_widget.setObjectName("settingsContent")
        content_layout = QVBoxLayout(content_widget)
        content_layout.setSpacing(16)
        content_layout.setContentsMargins(0, 0, 0, 0)
        self.create_settings_controls(content_layout)
        content_layout.addStretch(1)
        scroll_area.setWidget(content_widget)
        layout.addWidget(scroll_area, 1)

        button_layout = QHBoxLayout()
        self.save_button = QPushButton("Save")
        self.cancel_button = QPushButton("Cancel")
        self.save_button.clicked.connect(self.save_settings)
        self.cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(self.save_button)
        button_layout.addWidget(self.cancel_button)
        layout.addLayout(button_layout)

        self.load_saved_settings()
        self.apply_theme()

    def create_settings_controls(self, layout):
        source_layout = QHBoxLayout()
        source_layout.addWidget(QLabel("Default source folder:"))
        self.source_input = QLineEdit()
        source_button = QPushButton("Browse...")
        source_button.clicked.connect(self.browse_source)
        source_layout.addWidget(self.source_input)
        source_layout.addWidget(source_button)
        layout.addLayout(source_layout)

        dest_layout = QHBoxLayout()
        dest_layout.addWidget(QLabel("Destination folder:"))
        self.dest_input = QLineEdit()
        dest_button = QPushButton("Browse...")
        dest_button.clicked.connect(self.browse_destination)
        dest_layout.addWidget(self.dest_input)
        dest_layout.addWidget(dest_button)
        layout.addLayout(dest_layout)

        action_layout = QHBoxLayout()
        action_layout.addWidget(QLabel("File operation:"))
        self.copy_radio = QRadioButton("Copy")
        self.move_radio = QRadioButton("Move")
        action_layout.addWidget(self.copy_radio)
        action_layout.addWidget(self.move_radio)
        layout.addLayout(action_layout)

        theme_layout = QHBoxLayout()
        theme_layout.addWidget(QLabel("Theme:"))
        self.theme_combo = QComboBox()
        self.theme_combo.setView(QListView(self.theme_combo))
        self.theme_combo.addItems(["Auto", "Light", "Dark"])
        theme_layout.addWidget(self.theme_combo)
        layout.addLayout(theme_layout)

        thumb_layout = QHBoxLayout()
        thumb_layout.addWidget(QLabel("Thumbnail size:"))
        self.thumb_spin = QSpinBox()
        self.thumb_spin.setRange(32, 160)
        self.thumb_spin.setSingleStep(16)
        thumb_layout.addWidget(self.thumb_spin)
        layout.addLayout(thumb_layout)

        raw_layout = QHBoxLayout()
        raw_layout.addWidget(QLabel("RAW preview quality:"))
        self.raw_combo = QComboBox()
        self.raw_combo.setView(QListView(self.raw_combo))
        self.raw_combo.addItems(["Low", "Normal", "High"])
        raw_layout.addWidget(self.raw_combo)
        layout.addLayout(raw_layout)

        gamma_layout = QHBoxLayout()
        gamma_layout.addWidget(QLabel("RAW gamma:"))
        self.gamma_spin = QDoubleSpinBox()
        self.gamma_spin.setRange(1.0, 3.0)
        self.gamma_spin.setSingleStep(0.1)
        self.gamma_spin.setDecimals(1)
        gamma_layout.addWidget(self.gamma_spin)
        layout.addLayout(gamma_layout)

        pair_layout = QHBoxLayout()
        pair_layout.addWidget(QLabel("Combine paired RAW/JPG into one row:"))
        self.pair_checkbox = QCheckBox()
        pair_layout.addWidget(self.pair_checkbox)
        layout.addLayout(pair_layout)

        rename_layout = QHBoxLayout()
        rename_layout.addWidget(QLabel("Rename files using EXIF date:"))
        self.rename_checkbox = QCheckBox()
        rename_layout.addWidget(self.rename_checkbox)
        layout.addLayout(rename_layout)

        format_layout = QHBoxLayout()
        self.format_label = QLabel("Format preview: YYYYMMDD-HHMMSS")
        self.format_label.setEnabled(False)
        format_layout.addWidget(self.format_label)
        layout.addLayout(format_layout)
        self.rename_checkbox.stateChanged.connect(lambda state: self.format_label.setEnabled(state == Qt.Checked))

        subfolder_layout = QHBoxLayout()
        subfolder_layout.addWidget(QLabel("Include subfolders when loading images:"))
        self.subfolder_checkbox = QCheckBox()
        subfolder_layout.addWidget(self.subfolder_checkbox)
        layout.addLayout(subfolder_layout)

        self.create_editor_settings(layout)

    def create_editor_settings(self, layout):
        editor_group = QGroupBox("External Editor")
        editor_layout = QVBoxLayout(editor_group)

        editor_path_layout = QHBoxLayout()
        editor_path_layout.addWidget(QLabel("Editor Path:"))
        self.editor_path_input = QLineEdit()
        editor_browse_button = QPushButton("Browse...")
        editor_browse_button.clicked.connect(self.browse_editor)
        editor_path_layout.addWidget(self.editor_path_input)
        editor_path_layout.addWidget(editor_browse_button)
        editor_layout.addLayout(editor_path_layout)

        work_dir_layout = QHBoxLayout()
        work_dir_layout.addWidget(QLabel("Edit Directory:"))
        self.work_dir_input = QLineEdit()
        work_dir_browse_button = QPushButton("Browse...")
        work_dir_browse_button.clicked.connect(self.browse_work_dir)
        work_dir_layout.addWidget(self.work_dir_input)
        work_dir_layout.addWidget(work_dir_browse_button)
        editor_layout.addLayout(work_dir_layout)

        layout.addWidget(editor_group)

    def load_saved_settings(self):
        settings = self.settings_manager.current_settings
        self.source_input.setText(settings["default_source_folder"])
        self.dest_input.setText(settings["destination_folder"])
        self.move_radio.setChecked(settings["operation_mode"] == "move")
        self.copy_radio.setChecked(settings["operation_mode"] != "move")
        theme = settings["dark_mode"]
        if theme is None:
            self.theme_combo.setCurrentIndex(0)
        elif theme:
            self.theme_combo.setCurrentIndex(2)
        else:
            self.theme_combo.setCurrentIndex(1)
        self.thumb_spin.setValue(settings["thumbnail_size"])
        self.raw_combo.setCurrentText(settings["raw_preview_quality"].capitalize())
        self.gamma_spin.setValue(settings["raw_gamma"])
        self.pair_checkbox.setChecked(settings["auto_select_pairs"])
        self.rename_checkbox.setChecked(settings["rename_files"])
        self.format_label.setEnabled(settings["rename_files"])
        self.subfolder_checkbox.setChecked(settings["include_subfolders"])
        self.editor_path_input.setText(settings.get("editor_path", ""))
        self.work_dir_input.setText(settings.get("edit_directory", ""))

    def save_settings(self):
        theme_index = self.theme_combo.currentIndex()
        if theme_index == 0:
            dark_mode = None
        else:
            dark_mode = theme_index == 2

        settings = self.settings_manager.current_settings.copy()
        settings.update(
            {
                "default_source_folder": self.source_input.text().strip(),
                "destination_folder": self.dest_input.text().strip(),
                "operation_mode": "move" if self.move_radio.isChecked() else "copy",
                "dark_mode": dark_mode,
                "thumbnail_size": self.thumb_spin.value(),
                "raw_preview_quality": self.raw_combo.currentText().lower(),
                "raw_gamma": round(self.gamma_spin.value(), 1),
                "auto_select_pairs": self.pair_checkbox.isChecked(),
                "rename_files": self.rename_checkbox.isChecked(),
                "include_subfolders": self.subfolder_checkbox.isChecked(),
                "editor_path": self.editor_path_input.text().strip(),
                "edit_directory": self.work_dir_input.text().strip(),
            }
        )
        if self.settings_manager.save_settings(settings):
            self.accept()
            return
        QMessageBox.warning(self, "Error", "Failed to save settings")

    def browse_destination(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Destination Folder", "")
        if folder:
            self.dest_input.setText(folder)

    def browse_source(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Default Source Folder", "")
        if folder:
            self.source_input.setText(folder)

    def browse_editor(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Editor Executable", "", "Executables (*.exe);;All Files (*)")
        if file_path:
            self.editor_path_input.setText(file_path)

    def browse_work_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Working Directory", "")
        if folder:
            self.work_dir_input.setText(folder)

    def apply_theme(self):
        parent_window = self.parent()
        is_dark = parent_window._is_dark_mode if parent_window else safe_is_dark()
        safe_apply_mica(self, is_dark)
        down_icon = qss_icon_url("chevron-down-white.svg" if is_dark else "chevron-down-black.svg")
        spin_up_icon = qss_icon_url("chevron-up-white.svg" if is_dark else "chevron-up-black.svg")
        spin_down_icon = qss_icon_url("chevron-down-small-white.svg" if is_dark else "chevron-down-small-black.svg")
        if is_dark:
            self.setStyleSheet(
                """
                QDialog { background-color: #2b2b2b; color: #ffffff; }
                QLabel, QGroupBox { color: #ffffff; }
                QScrollArea, QScrollArea > QWidget > QWidget, #settingsContent {
                    background-color: #2b2b2b;
                    border: none;
                }
                QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                    background-color: #1e1e1e;
                    color: #ffffff;
                    border: 1px solid #3f3f3f;
                    border-radius: 4px;
                    padding: 4px;
                }
                QComboBox::drop-down {
                    background-color: #252525;
                    border-left: 1px solid #3f3f3f;
                    width: 24px;
                }
                QComboBox::down-arrow {
                    image: url(__DOWN_ICON__);
                    width: 12px;
                    height: 12px;
                }
                QSpinBox::up-button, QSpinBox::down-button,
                QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                    background-color: #252525;
                    border: none;
                    width: 20px;
                }
                QSpinBox::up-button, QDoubleSpinBox::up-button {
                    border-top-right-radius: 4px;
                }
                QSpinBox::down-button, QDoubleSpinBox::down-button {
                    border-bottom-right-radius: 4px;
                }
                QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
                    image: url(__SPIN_UP_ICON__);
                    width: 10px;
                    height: 10px;
                }
                QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
                    image: url(__SPIN_DOWN_ICON__);
                    width: 10px;
                    height: 10px;
                }
                QComboBox QAbstractItemView {
                    background-color: #1e1e1e;
                    color: #ffffff;
                    border: 1px solid #3f3f3f;
                    selection-background-color: #0078d4;
                    selection-color: #ffffff;
                    outline: none;
                }
                QPushButton {
                    background-color: #303030;
                    color: #ffffff;
                    border: 1px solid #3f3f3f;
                    border-radius: 5px;
                    padding: 6px 12px;
                }
                QPushButton:hover { background-color: #3a3a3a; }
                """
                .replace("__DOWN_ICON__", down_icon)
                .replace("__SPIN_UP_ICON__", spin_up_icon)
                .replace("__SPIN_DOWN_ICON__", spin_down_icon)
            )
        else:
            self.setStyleSheet(
                """
                QDialog { background-color: #f9f9f9; color: #000000; }
                QScrollArea, QScrollArea > QWidget > QWidget, #settingsContent {
                    background-color: #f9f9f9;
                    border: none;
                }
                QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                    background-color: #ffffff;
                    color: #000000;
                    border: 1px solid #d9d9d9;
                    border-radius: 4px;
                    padding: 4px;
                }
                QComboBox::drop-down {
                    background-color: #f3f3f3;
                    border-left: 1px solid #d9d9d9;
                    border-top-right-radius: 4px;
                    border-bottom-right-radius: 4px;
                    width: 24px;
                }
                QComboBox::down-arrow {
                    image: url(__DOWN_ICON__);
                    width: 12px;
                    height: 12px;
                }
                QSpinBox::up-button, QSpinBox::down-button,
                QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                    background-color: #f3f3f3;
                    border: none;
                    width: 20px;
                }
                QSpinBox::up-button, QDoubleSpinBox::up-button {
                    border-top-right-radius: 4px;
                }
                QSpinBox::down-button, QDoubleSpinBox::down-button {
                    border-bottom-right-radius: 4px;
                }
                QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
                    image: url(__SPIN_UP_ICON__);
                    width: 10px;
                    height: 10px;
                }
                QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
                    image: url(__SPIN_DOWN_ICON__);
                    width: 10px;
                    height: 10px;
                }
                QPushButton {
                    background-color: #f3f3f3;
                    color: #000000;
                    border: 1px solid #d9d9d9;
                    border-radius: 5px;
                    padding: 6px 12px;
                }
                QPushButton:hover { background-color: #ececec; }
                """
                .replace("__DOWN_ICON__", down_icon)
                .replace("__SPIN_UP_ICON__", spin_up_icon)
                .replace("__SPIN_DOWN_ICON__", spin_down_icon)
            )
        self.apply_combo_popup_theme(is_dark)

    def apply_combo_popup_theme(self, is_dark):
        down_icon = qss_icon_url("chevron-down-white.svg" if is_dark else "chevron-down-black.svg")
        if is_dark:
            combo_style = """
                QComboBox {
                    background-color: #1e1e1e;
                    color: #ffffff;
                    border: 1px solid #3f3f3f;
                    border-radius: 4px;
                    padding: 4px 28px 4px 4px;
                }
                QComboBox:hover {
                    border-color: #5a5a5a;
                }
                QComboBox::drop-down {
                    background-color: #252525;
                    border-left: 1px solid #3f3f3f;
                    border-top-right-radius: 4px;
                    border-bottom-right-radius: 4px;
                    width: 24px;
                }
                QComboBox::down-arrow {
                    image: url(__DOWN_ICON__);
                    width: 12px;
                    height: 12px;
                }
                QComboBox QListView {
                    background-color: #1e1e1e;
                    color: #ffffff;
                    border: 1px solid #3f3f3f;
                    selection-background-color: #0078d4;
                    selection-color: #ffffff;
                    outline: none;
                }
                QComboBox QListView::item {
                    background-color: #1e1e1e;
                    color: #ffffff;
                    padding: 3px 4px;
                }
                QComboBox QListView::item:selected {
                    background-color: #0078d4;
                    color: #ffffff;
                }
            """.replace("__DOWN_ICON__", down_icon)
            view_style = """
                QListView {
                    background-color: #1e1e1e;
                    color: #ffffff;
                    border: 1px solid #3f3f3f;
                    selection-background-color: #0078d4;
                    selection-color: #ffffff;
                    outline: none;
                }
                QListView::item {
                    background-color: #1e1e1e;
                    color: #ffffff;
                    padding: 3px 4px;
                }
                QListView::item:selected {
                    background-color: #0078d4;
                    color: #ffffff;
                }
            """
        else:
            combo_style = """
                QComboBox {
                    background-color: #ffffff;
                    color: #000000;
                    border: 1px solid #d9d9d9;
                    border-radius: 4px;
                    padding: 4px 28px 4px 4px;
                }
                QComboBox:hover {
                    border-color: #b8b8b8;
                }
                QComboBox::drop-down {
                    background-color: #f3f3f3;
                    border-left: 1px solid #d9d9d9;
                    border-top-right-radius: 4px;
                    border-bottom-right-radius: 4px;
                    width: 24px;
                }
                QComboBox::down-arrow {
                    image: url(__DOWN_ICON__);
                    width: 12px;
                    height: 12px;
                }
                QComboBox QListView {
                    background-color: #ffffff;
                    color: #000000;
                    border: 1px solid #d9d9d9;
                    selection-background-color: #cce4ff;
                    selection-color: #000000;
                    outline: none;
                }
                QComboBox QListView::item {
                    background-color: #ffffff;
                    color: #000000;
                    padding: 3px 4px;
                }
                QComboBox QListView::item:selected {
                    background-color: #cce4ff;
                    color: #000000;
                }
            """.replace("__DOWN_ICON__", down_icon)
            view_style = """
                QListView {
                    background-color: #ffffff;
                    color: #000000;
                    border: 1px solid #d9d9d9;
                    selection-background-color: #cce4ff;
                    selection-color: #000000;
                    outline: none;
                }
                QListView::item {
                    background-color: #ffffff;
                    color: #000000;
                    padding: 3px 4px;
                }
                QListView::item:selected {
                    background-color: #cce4ff;
                    color: #000000;
                }
            """

        for combo in (self.theme_combo, self.raw_combo):
            combo.setStyleSheet(combo_style)
            combo.view().setStyleSheet(view_style)


class FileOperationDialog(QDialog):
    def __init__(self, parent=None, operation="", total_files=0):
        super().__init__(parent)
        self._cancelled = False
        title = "Moving Files" if operation.lower() == "move" else f"{operation.capitalize()}ing Files"
        self.setWindowTitle(title)
        self.setFixedSize(420, 160)
        self.setWindowModality(Qt.WindowModal)

        layout = QVBoxLayout(self)
        self.status_label = QLabel("Preparing...")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, max(total_files, 1))
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel)

        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.cancel_button)

        self.apply_theme()

    def apply_theme(self):
        parent_window = self.parent()
        is_dark = parent_window._is_dark_mode if parent_window else safe_is_dark()
        safe_apply_mica(self, is_dark)
        if is_dark:
            self.setStyleSheet(
                """
                QDialog { background-color: #2b2b2b; color: #ffffff; }
                QLabel { color: #ffffff; }
                QPushButton {
                    background-color: #303030;
                    color: #ffffff;
                    border: 1px solid #3f3f3f;
                    border-radius: 5px;
                    padding: 6px 12px;
                }
                QPushButton:hover { background-color: #3a3a3a; }
                QProgressBar {
                    border: 1px solid #3f3f3f;
                    border-radius: 5px;
                    text-align: center;
                    background-color: #1e1e1e;
                }
                QProgressBar::chunk { background-color: #0078d4; }
                """
            )
        else:
            self.setStyleSheet(
                """
                QDialog { background-color: #f9f9f9; color: #000000; }
                QPushButton {
                    background-color: #f3f3f3;
                    color: #000000;
                    border: 1px solid #d9d9d9;
                    border-radius: 5px;
                    padding: 6px 12px;
                }
                QPushButton:hover { background-color: #ececec; }
                QProgressBar {
                    border: 1px solid #d9d9d9;
                    border-radius: 5px;
                    text-align: center;
                    background-color: #ffffff;
                }
                QProgressBar::chunk { background-color: #0078d4; }
                """
            )

    def cancel(self):
        self._cancelled = True

    def is_cancelled(self):
        return self._cancelled

    def update_status(self, current_file, total_files, file_name=""):
        if file_name:
            self.status_label.setText(f"Processing {current_file} of {total_files}: {file_name}")
        else:
            self.status_label.setText(f"Processing {current_file} of {total_files}")
        self.progress_bar.setMaximum(max(total_files, 1))
        self.progress_bar.setValue(current_file)


class Win11MessageBox(QDialog):
    def __init__(self, parent=None, title="", message="", buttons=QMessageBox.Ok, icon=QMessageBox.Information):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModal)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        content_layout = QHBoxLayout()
        icon_pixmap = self.standard_icon(icon)
        if icon_pixmap is not None:
            icon_label = QLabel()
            icon_label.setPixmap(icon_pixmap.pixmap(32, 32))
            icon_label.setAlignment(Qt.AlignTop)
            content_layout.addWidget(icon_label)
        self.message_label = QLabel(message)
        self.message_label.setWordWrap(True)
        self.message_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        content_layout.addWidget(self.message_label, 1)
        layout.addLayout(content_layout)

        button_layout = QHBoxLayout()
        self.buttons = []
        self.button_ids = []
        for button_id, label in (
            (QMessageBox.Yes, "Yes"),
            (QMessageBox.YesToAll, "Yes to All"),
            (QMessageBox.No, "No"),
            (QMessageBox.NoToAll, "No to All"),
            (QMessageBox.Ok, "OK"),
        ):
            if buttons & button_id:
                button = QPushButton(label)
                button.clicked.connect(lambda _, value=button_id: self.done(value))
                self.buttons.append(button)
                self.button_ids.append(button_id)
                button_layout.addWidget(button)
        layout.addLayout(button_layout)
        layout.addStretch(1)

        self.apply_theme()

    def standard_icon(self, icon):
        icon_map = {
            QMessageBox.Information: QStyle.SP_MessageBoxInformation,
            QMessageBox.Warning: QStyle.SP_MessageBoxWarning,
            QMessageBox.Critical: QStyle.SP_MessageBoxCritical,
            QMessageBox.Question: QStyle.SP_MessageBoxQuestion,
        }
        standard_id = icon_map.get(icon)
        if standard_id is None:
            return None
        return self.style().standardIcon(standard_id)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            if QMessageBox.No in self.button_ids:
                self.done(QMessageBox.No)
            else:
                self.done(QMessageBox.Rejected)
            return
        super().keyPressEvent(event)

    def apply_theme(self):
        parent_window = self.parent()
        is_dark = parent_window._is_dark_mode if parent_window else safe_is_dark()
        safe_apply_mica(self, is_dark)
        if is_dark:
            self.setStyleSheet(
                """
                QDialog { background-color: #2b2b2b; color: #ffffff; }
                QLabel { color: #ffffff; }
                QPushButton {
                    background-color: #303030;
                    color: #ffffff;
                    border: 1px solid #3f3f3f;
                    border-radius: 5px;
                    padding: 6px 12px;
                    min-width: 90px;
                }
                QPushButton:hover { background-color: #3a3a3a; }
                """
            )
        else:
            self.setStyleSheet(
                """
                QDialog { background-color: #f9f9f9; color: #000000; }
                QPushButton {
                    background-color: #f3f3f3;
                    color: #000000;
                    border: 1px solid #d9d9d9;
                    border-radius: 5px;
                    padding: 6px 12px;
                    min-width: 90px;
                }
                QPushButton:hover { background-color: #ececec; }
                """
            )


class ImageTableModel(QAbstractTableModel):
    headers = [" ", "Thumbnail", "Filename", "Date", "Time", "F-Stop", "Shutter", "ISO", "FL", "Lens"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries = []
        self.checked_entry_ids = set()
        self.sort_column = 2
        self.sort_order = Qt.AscendingOrder
        self.defer_sort = False
        self._row_index = {}

    def _rebuild_row_index(self):
        self._row_index = {
            entry.get("entry_id"): row
            for row, entry in enumerate(self.entries)
            if entry.get("entry_id")
        }

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal and 0 <= section < len(self.headers):
            return self.headers[section]
        if orientation == Qt.Vertical:
            return str(section + 1)
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == 0:
            flags |= Qt.ItemIsUserCheckable
        return flags

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self.entries):
            return None
        entry = self.entries[index.row()]
        column = index.column()
        if column == 0 and role == Qt.CheckStateRole:
            return Qt.Checked if entry.get("entry_id") in self.checked_entry_ids else Qt.Unchecked
        if column == 1:
            if role == Qt.DecorationRole:
                return entry.get("thumbnail")
            return None
        if role not in (Qt.DisplayRole, Qt.EditRole):
            if role == Qt.UserRole + 1:
                return entry
            return None
        if column == 2:
            return entry.get("display_name", "")
        exif_data = entry.get("exif") or {}
        if column == 3:
            return exif_data.get("date", "")
        if column == 4:
            return exif_data.get("time", "")
        if column == 5:
            return exif_data.get("f_stop", "")
        if column == 6:
            return exif_data.get("exposure", "")
        if column == 7:
            return exif_data.get("iso", "")
        if column == 8:
            return exif_data.get("focal_length", "")
        if column == 9:
            return exif_data.get("lens_model", "")
        return ""

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid() or index.column() != 0 or role != Qt.CheckStateRole:
            return False
        entry_id = self.entries[index.row()].get("entry_id")
        if not entry_id:
            return False
        if value == Qt.Checked:
            self.checked_entry_ids.add(entry_id)
        else:
            self.checked_entry_ids.discard(entry_id)
        self.dataChanged.emit(index, index, [Qt.CheckStateRole])
        return True

    def clear(self):
        self.beginResetModel()
        self.entries.clear()
        self.checked_entry_ids.clear()
        self._row_index.clear()
        self.endResetModel()

    def add_entries(self, entries, apply_sort=True):
        if not entries:
            return
        start_row = len(self.entries)
        end_row = start_row + len(entries) - 1
        self.beginInsertRows(QModelIndex(), start_row, end_row)
        self.entries.extend(entries)
        self._rebuild_row_index()
        self.endInsertRows()
        if apply_sort and not self.defer_sort:
            self.apply_current_sort()

    def entry_at(self, row):
        if 0 <= row < len(self.entries):
            return self.entries[row]
        return None

    def row_for_entry_id(self, entry_id):
        if not entry_id:
            return -1
        return self._row_index.get(entry_id, -1)

    def update_entry_metadata(self, entry_id, exif_data, thumbnail_image):
        row = self.row_for_entry_id(entry_id)
        if row < 0:
            return
        entry = self.entries[row]
        entry["exif"] = exif_data or {}
        if thumbnail_image and not thumbnail_image.isNull():
            entry["thumbnail"] = QPixmap.fromImage(thumbnail_image)
        if not self.defer_sort and self.sort_column in (3, 4, 5, 6, 7, 8, 9):
            if self.apply_current_sort():
                return
        top_left = self.index(row, 1)
        bottom_right = self.index(row, len(self.headers) - 1)
        self.dataChanged.emit(top_left, bottom_right, [Qt.DisplayRole, Qt.DecorationRole])

    def set_all_checked(self, checked):
        if checked:
            self.checked_entry_ids = {entry.get("entry_id") for entry in self.entries if entry.get("entry_id")}
        else:
            self.checked_entry_ids.clear()
        if self.entries:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.entries) - 1, 0), [Qt.CheckStateRole])

    def selected_entries(self):
        return [(row, entry) for row, entry in enumerate(self.entries) if entry.get("entry_id") in self.checked_entry_ids]

    def sort(self, column, order=Qt.AscendingOrder):
        if not 0 <= column < len(self.headers):
            return
        self.sort_column = column
        self.sort_order = order
        self.apply_current_sort()

    def apply_current_sort(self):
        if len(self.entries) < 2:
            return False
        reverse = self.sort_order == Qt.DescendingOrder
        sorted_entries = sorted(self.entries, key=self.sort_key, reverse=reverse)
        if sorted_entries == self.entries:
            return False
        self.layoutAboutToBeChanged.emit()
        self.entries = sorted_entries
        self._rebuild_row_index()
        self.layoutChanged.emit()
        return True

    def sort_key(self, entry):
        exif_data = entry.get("exif") or {}
        column = self.sort_column
        if column == 0:
            return (entry.get("entry_id") not in self.checked_entry_ids, self.filename_key(entry))
        if column == 1:
            thumbnail = entry.get("thumbnail")
            has_thumbnail = bool(thumbnail is not None and not thumbnail.isNull())
            return (not has_thumbnail, self.filename_key(entry))
        if column == 2:
            return self.filename_key(entry)
        if column in (3, 4):
            return self.datetime_key(entry, exif_data)
        if column == 5:
            return self.number_key(exif_data.get("f_stop"), entry)
        if column == 6:
            exposure = exif_data.get("exposure_seconds")
            if exposure is None:
                exposure = parse_exposure_seconds(exif_data.get("exposure"))
            return self.number_key(exposure, entry)
        if column == 7:
            return self.number_key(exif_data.get("iso"), entry)
        if column == 8:
            return self.number_key(exif_data.get("focal_length"), entry)
        if column == 9:
            return (str(exif_data.get("lens_model", "")).casefold(), self.filename_key(entry))
        return self.filename_key(entry)

    def number_key(self, value, entry):
        number = value if isinstance(value, (int, float)) else parse_leading_number(value)
        return (number is None, number if number is not None else 0, self.filename_key(entry))

    def filename_key(self, entry):
        return natural_sort_key(entry.get("display_name", ""))

    def datetime_key(self, entry, exif_data):
        dt_obj = parse_exif_datetime(exif_data.get("datetime_original", ""))
        if dt_obj:
            return (0, dt_obj.timestamp(), self.filename_key(entry))
        modified_time = entry.get("modified_time", 0) or 0
        return (1, modified_time, self.filename_key(entry))


class ThumbnailDelegate(QStyledItemDelegate):
    def __init__(self, thumbnail_size=64, parent=None):
        super().__init__(parent)
        self.thumbnail_size = QSize(thumbnail_size, thumbnail_size)

    def set_thumbnail_size(self, thumbnail_size):
        self.thumbnail_size = QSize(thumbnail_size, thumbnail_size)

    def paint(self, painter, option, index):
        if index.column() != 1:
            super().paint(painter, option, index)
            return

        pixmap = index.data(Qt.DecorationRole)
        if pixmap is None or pixmap.isNull():
            super().paint(painter, option, index)
            return

        # Let the style draw the row background/selection, then paint the
        # thumbnail centered at its own aspect ratio (never stretched).
        style_option = QStyleOptionViewItem(option)
        self.initStyleOption(style_option, index)
        style_option.text = ""
        style_option.icon = QIcon()
        widget = style_option.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, style_option, painter, widget)

        scaled = pixmap
        if pixmap.width() > self.thumbnail_size.width() or pixmap.height() > self.thumbnail_size.height():
            scaled = pixmap.scaled(self.thumbnail_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x_pos = option.rect.x() + (option.rect.width() - scaled.width()) // 2
        y_pos = option.rect.y() + (option.rect.height() - scaled.height()) // 2
        painter.drawPixmap(x_pos, y_pos, scaled)

    def sizeHint(self, option, index):
        base_size = super().sizeHint(option, index)
        return QSize(base_size.width(), max(base_size.height(), self.thumbnail_size.height() + 8))


class Win11CheckBoxDelegate(QStyledItemDelegate):
    def __init__(self, dark_getter, parent=None):
        super().__init__(parent)
        self.dark_getter = dark_getter

    def createEditor(self, parent, option, index):
        return None

    def paint(self, painter, option, index):
        is_dark = self.dark_getter()
        border_color = QColor("#ffffff" if is_dark else "#000000")
        hover_color = QColor("#3f3f3f" if is_dark else "#f0f0f0")

        painter.save()
        if option.state & QStyle.State_MouseOver:
            painter.fillRect(option.rect, hover_color)
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())

        checked = index.data(Qt.CheckStateRole) == Qt.Checked
        box_size = 18
        x_pos = option.rect.x() + (option.rect.width() - box_size) // 2
        y_pos = option.rect.y() + (option.rect.height() - box_size) // 2
        checkbox_rect = QRect(x_pos, y_pos, box_size, box_size)

        painter.setPen(border_color)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(checkbox_rect, 4, 4)

        if checked:
            pen = QPen(border_color)
            pen.setWidth(2)
            painter.setPen(pen)
            checkmark_points = [
                QPoint(checkbox_rect.left() + 4, checkbox_rect.top() + 9),
                QPoint(checkbox_rect.left() + 8, checkbox_rect.bottom() - 4),
                QPoint(checkbox_rect.right() - 4, checkbox_rect.top() + 4),
            ]
            painter.drawPolyline(QPolygon(checkmark_points))

        painter.restore()

    def editorEvent(self, event, model, option, index):
        if event.type() in (QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick):
            current_state = index.data(Qt.CheckStateRole)
            next_state = Qt.Checked if current_state != Qt.Checked else Qt.Unchecked
            model.setData(index, next_state, Qt.CheckStateRole)
            return True
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Space:
            current_state = index.data(Qt.CheckStateRole)
            next_state = Qt.Checked if current_state != Qt.Checked else Qt.Unchecked
            model.setData(index, next_state, Qt.CheckStateRole)
            return True
        return False


class ImageLoaderThread(QThread):
    progress_signal = pyqtSignal(int, int)
    entries_loaded_signal = pyqtSignal(list)
    finished_signal = pyqtSignal()
    status_signal = pyqtSignal(str)

    def __init__(self, directory, include_subfolders, combine_pairs):
        super().__init__()
        self.directory = directory
        self.include_subfolders = include_subfolders
        self.combine_pairs = combine_pairs
        self.running = True
        self.cancelled = False

    def cancel(self):
        self.cancelled = True
        self.running = False

    def _should_continue(self):
        return self.running

    def _scan_progress(self, scanned_count):
        if self.running:
            self.status_signal.emit(f"Scanning... {scanned_count} files found")

    def build_entries(self):
        file_records = scan_image_files(self.directory, self.include_subfolders, self._should_continue)
        if self.combine_pairs:
            return build_grouped_entries(file_records)
        return [build_single_entry(record) for record in file_records]

    def _emit_entries(self, entries):
        batch = []
        for index, entry in enumerate(entries, start=1):
            if not self.running:
                return
            batch.append(entry)
            if len(batch) >= 64:
                self.entries_loaded_signal.emit(batch)
                batch = []
            self.progress_signal.emit(index, len(entries))
            if index == 1 or index % 100 == 0:
                self.status_signal.emit(f"Found {index} of {len(entries)} entries")
        if batch and self.running:
            self.entries_loaded_signal.emit(batch)

    def _stream_single_entries(self):
        batch = []
        count = 0
        for record in iter_image_files(
            self.directory,
            self.include_subfolders,
            self._should_continue,
            self._scan_progress,
        ):
            if not self.running:
                return
            batch.append(build_single_entry(record))
            count += 1
            if len(batch) >= 64:
                self.status_signal.emit(f"Scanning... {count} files found")
                self.entries_loaded_signal.emit(batch)
                batch = []
        if batch and self.running:
            self.entries_loaded_signal.emit(batch)
        if self.running:
            self.status_signal.emit(f"Found {count} entries")

    def run(self):
        try:
            if self.combine_pairs:
                # Pairing needs the complete file list before rows can be
                # grouped, so scan first and then publish in batches.
                file_records = scan_image_files(self.directory, self.include_subfolders, self._should_continue)
                if not self.running:
                    return
                self._emit_entries(build_grouped_entries(file_records))
            else:
                self._stream_single_entries()
        except Exception:
            LOGGER.exception("Image loader thread failed")
        finally:
            self.finished_signal.emit()


class MetadataLoaderThread(QThread):
    metadata_ready_signal = pyqtSignal(str, dict, QImage)

    def __init__(self, thumbnail_size):
        super().__init__()
        self.thumbnail_size = thumbnail_size
        self.running = True
        self._condition = threading.Condition()
        self._pending = deque()
        self._pending_ids = set()
        self._inflight_ids = set()
        self._completed_ids = set()

    def cancel(self):
        with self._condition:
            self.running = False
            self._pending.clear()
            self._pending_ids.clear()
            self._condition.notify_all()

    def add_entries(self, entries, priority=False):
        new_entries = []
        with self._condition:
            if not self.running:
                return
            for entry in entries:
                entry_id = entry.get("entry_id")
                if not entry_id:
                    continue
                if entry_id in self._pending_ids or entry_id in self._inflight_ids or entry_id in self._completed_ids:
                    continue
                self._pending_ids.add(entry_id)
                new_entries.append(entry)
            if not new_entries:
                return
            if priority:
                for entry in reversed(new_entries):
                    self._pending.appendleft(entry)
            else:
                self._pending.extend(new_entries)
            self._condition.notify()

    def _next_entry(self):
        with self._condition:
            while self.running and not self._pending:
                self._condition.wait(0.5)
            if not self.running:
                return None
            entry = self._pending.popleft()
            entry_id = entry.get("entry_id")
            self._pending_ids.discard(entry_id)
            self._inflight_ids.add(entry_id)
            return entry

    def _finish_entry(self, entry_id):
        with self._condition:
            self._inflight_ids.discard(entry_id)
            if entry_id:
                self._completed_ids.add(entry_id)

    def run(self):
        while self.running:
            entry = self._next_entry()
            if entry is None:
                break
            entry_id = entry.get("entry_id")
            preview_path = choose_preview_path(entry, show_raw=False)
            if not preview_path:
                self._finish_entry(entry_id)
                self.metadata_ready_signal.emit(entry_id, {}, QImage())
                continue
            exif_data = extract_exif(preview_path)
            if not self.running:
                break
            thumbnail = create_thumbnail_image(preview_path, self.thumbnail_size)
            if not self.running:
                break
            self._finish_entry(entry_id)
            self.metadata_ready_signal.emit(entry_id, exif_data, thumbnail)


class ImagePreviewThread(QThread):
    finished_signal = pyqtSignal(int, str, QImage)
    error_signal = pyqtSignal(int, str, str)

    def __init__(self, request_id, file_path, target_size, high_quality=True):
        super().__init__()
        self.request_id = request_id
        self.file_path = file_path
        self.target_size = target_size
        self.high_quality = high_quality
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            if self._cancelled:
                return
            reader = QImageReader(self.file_path)
            reader.setAutoTransform(True)
            source_size = reader.size()
            if source_size.isValid():
                source_size.scale(self.target_size, Qt.KeepAspectRatio)
                reader.setScaledSize(source_size)
            image = reader.read()
            if self._cancelled:
                return
            if image.isNull():
                self.error_signal.emit(self.request_id, self.file_path, reader.errorString() or "Unsupported image")
                return
            scaled = scaled_image_for_size(image, self.target_size, smooth=self.high_quality)
            if self._cancelled:
                return
            self.finished_signal.emit(self.request_id, self.file_path, scaled)
        except Exception as exc:
            LOGGER.exception("Preview failed for %s", self.file_path)
            self.error_signal.emit(self.request_id, self.file_path, str(exc))


class RawPreviewThread(QThread):
    finished_signal = pyqtSignal(int, str, QImage)
    error_signal = pyqtSignal(int, str, str)

    def __init__(self, request_id, file_path, target_size, quality, gamma):
        super().__init__()
        self.request_id = request_id
        self.file_path = file_path
        self.target_size = target_size
        self.quality = quality
        self.gamma = gamma
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            if self._cancelled:
                return
            with rawpy.imread(self.file_path) as raw:
                kwargs = {"output_bps": 8, "gamma": (self.gamma, self.gamma)}
                if self.quality == "low":
                    kwargs.update({"half_size": True, "use_camera_wb": False, "no_auto_bright": True})
                elif self.quality == "high":
                    kwargs.update({"half_size": False, "use_camera_wb": True, "no_auto_bright": False})
                else:
                    kwargs.update({"half_size": False, "use_camera_wb": True, "no_auto_bright": True})
                rgb = raw.postprocess(**kwargs)
            if self._cancelled:
                return
            height, width, channels = rgb.shape
            qimg = QImage(rgb.data, width, height, width * channels, QImage.Format_RGB888).copy()
            scaled = scaled_image_for_size(qimg, self.target_size)
            if self._cancelled:
                return
            self.finished_signal.emit(self.request_id, self.file_path, scaled)
        except Exception as exc:
            LOGGER.exception("RAW preview failed for %s", self.file_path)
            self.error_signal.emit(self.request_id, self.file_path, str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings_manager = SettingsManager()
        self._is_dark_mode = theme_is_dark(self.settings_manager.get_setting("dark_mode"))
        self.current_folder = self.resolve_start_folder()
        self.current_entry_id = None
        self.current_image_path = None
        self.show_raw = False
        self.loading_in_progress = False
        self.file_operation_active = False
        self.preview_request_id = 0
        self.preview_thread = None
        self.preview_threads = set()
        self.loader_thread = None
        self.metadata_thread = None
        self.exif_cache = OrderedDict()
        self.raw_cache = OrderedDict()
        self._initial_load_started = False
        self._closing = False

        self.setWindowTitle(APP_DISPLAY_NAME)
        self.resize(1440, 860)
        self.setMinimumSize(900, 540)
        icon_path = Path(__file__).with_name("ImagePicker1.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.build_ui()
        self.apply_theme()
        self.show()
        QTimer.singleShot(100, self.load_initial_folder)

    def load_initial_folder(self):
        if self._initial_load_started or self.list_model.rowCount():
            return
        self.load_image_files(self.current_folder)

    def resolve_start_folder(self):
        default_folder = self.settings_manager.get_setting("default_source_folder")
        if default_folder and os.path.isdir(default_folder):
            return default_folder
        folder = QFileDialog.getExistingDirectory(self, "Select Image Folder", "")
        if folder:
            return folder
        return os.getcwd()

    def build_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        self.content_splitter = QSplitter(Qt.Horizontal)
        self.content_splitter.setChildrenCollapsible(False)
        self.content_splitter.setHandleWidth(8)
        main_layout.addWidget(self.content_splitter, 1)

        self.list_view = QTableView()
        self.list_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.list_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.list_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_view.setAlternatingRowColors(True)
        self.list_view.setShowGrid(False)
        self.list_view.verticalHeader().setVisible(True)
        self.list_view.setSortingEnabled(True)

        self.list_model = ImageTableModel(self.list_view)
        self.list_view.setModel(self.list_model)

        thumb_size = self.settings_manager.get_setting("thumbnail_size")
        self.thumbnail_delegate = ThumbnailDelegate(thumb_size, self.list_view)
        self.checkbox_delegate = Win11CheckBoxDelegate(lambda: self._is_dark_mode, self.list_view)
        self.list_view.setItemDelegateForColumn(0, self.checkbox_delegate)
        self.list_view.setItemDelegateForColumn(1, self.thumbnail_delegate)
        self.setup_table_headers()
        self.list_view.verticalHeader().setDefaultSectionSize(thumb_size + 8)
        self.list_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.list_view.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.list_view.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.list_view.selectionModel().selectionChanged.connect(self.display_selected_image)
        self.list_model.dataChanged.connect(self.handle_model_changed)
        self.list_model.layoutChanged.connect(self.restore_current_selection_after_sort)
        self.list_view.verticalScrollBar().valueChanged.connect(lambda _: QTimer.singleShot(0, self.queue_visible_metadata))

        self.content_splitter.addWidget(self.list_view)
        self.list_view.installEventFilter(self)

        self.preview_panel = QWidget()
        self.preview_panel.setObjectName("previewPanel")
        self.preview_panel.setAutoFillBackground(True)
        preview_layout = QVBoxLayout(self.preview_panel)
        preview_layout.setContentsMargins(12, 12, 12, 12)
        preview_layout.setSpacing(8)
        self.preview_title = QLabel("Preview")
        self.preview_label = QLabel("No image selected")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(1, 1)
        self.preview_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.preview_panel.setMinimumWidth(160)
        self.list_view.setMinimumWidth(240)
        preview_layout.addWidget(self.preview_title)
        preview_layout.addWidget(self.preview_label, 1)
        self.preview_label.installEventFilter(self)
        self.content_splitter.addWidget(self.preview_panel)
        self.content_splitter.setStretchFactor(0, 5)
        self.content_splitter.setStretchFactor(1, 4)
        self.content_splitter.setSizes([800, 640])
        self.content_splitter.splitterMoved.connect(self.schedule_preview_resize)

        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        main_layout.addLayout(button_layout)

        self.select_folder_button = QPushButton("Select Folder")
        self.select_folder_button.clicked.connect(self.select_folder)
        self.select_all_button = QPushButton("Select All")
        self.select_all_button.clicked.connect(self.select_all_files)
        self.select_none_button = QPushButton("Select None")
        self.select_none_button.clicked.connect(self.select_none_files)
        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(self.show_settings)
        self.process_button = QPushButton(self.settings_manager.get_setting("operation_mode").capitalize())
        self.process_button.clicked.connect(self.process_selected_files)
        self.edit_button = QPushButton("Edit")
        self.edit_button.clicked.connect(self.edit_current_image)
        self.toggle_button = QPushButton("Show RAW")
        self.toggle_button.setCheckable(True)
        self.toggle_button.clicked.connect(self.toggle_preview_mode)

        self.buttons = [
            self.select_folder_button,
            self.select_all_button,
            self.select_none_button,
            self.settings_button,
            self.process_button,
            self.edit_button,
            self.toggle_button,
        ]
        for button in self.buttons:
            button_layout.addWidget(button)

        self.status_progress = QProgressBar()
        self.status_progress.setFixedSize(220, 16)
        self.status_progress.setVisible(False)
        self.statusBar().addPermanentWidget(self.status_progress)

        self.resize_timer = QTimer(self)
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.reload_preview_after_resize)

    def setup_table_headers(self):
        self.list_view.horizontalHeader().setSortIndicatorShown(True)
        self.list_view.horizontalHeader().setSortIndicator(self.list_model.sort_column, self.list_model.sort_order)
        self.list_view.setColumnWidth(0, 28)
        self.list_view.setColumnWidth(1, max(72, self.thumbnail_delegate.thumbnail_size.width() + 16))
        self.list_view.setColumnWidth(3, 80)
        self.list_view.setColumnWidth(4, 80)
        self.list_view.setColumnWidth(5, 70)
        self.list_view.setColumnWidth(6, 80)
        self.list_view.setColumnWidth(7, 70)
        self.list_view.setColumnWidth(8, 70)
        self.list_view.setColumnWidth(9, 160)

    def eventFilter(self, obj, event):
        if obj is self.list_view and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Space:
            return self.toggle_current_selection()
        if obj is self.preview_label:
            if event.type() == QEvent.Resize:
                self.schedule_preview_resize()
                return False
            if event.type() == QEvent.Wheel:
                delta = event.angleDelta().y()
                if delta:
                    self.select_relative_row(-1 if delta > 0 else 1)
                    return True
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.MiddleButton:
                return self.toggle_current_selection()
        return False

    def schedule_preview_resize(self, *args):
        if self.current_image_path:
            self.resize_timer.start(120)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space and self.toggle_current_selection():
            return
        super().keyPressEvent(event)

    def current_row(self):
        current_index = self.list_view.currentIndex()
        if current_index.isValid():
            return current_index.row()
        selected_rows = self.list_view.selectionModel().selectedRows()
        if selected_rows:
            return selected_rows[0].row()
        return -1

    def toggle_current_selection(self):
        row = self.current_row()
        if row < 0:
            return False
        checkbox_index = self.list_model.index(row, 0)
        current_state = self.list_model.data(checkbox_index, Qt.CheckStateRole)
        next_state = Qt.Unchecked if current_state == Qt.Checked else Qt.Checked
        self.list_model.setData(checkbox_index, next_state, Qt.CheckStateRole)
        return True

    def select_relative_row(self, step):
        row_count = self.list_model.rowCount()
        if not row_count:
            return False
        current_row = self.current_row()
        if current_row < 0:
            target_row = 0
        else:
            target_row = max(0, min(row_count - 1, current_row + step))
        if target_row == current_row:
            return True
        self.list_view.selectRow(target_row)
        target_index = self.list_model.index(target_row, 2)
        if target_index.isValid():
            self.list_view.setCurrentIndex(target_index)
            self.list_view.scrollTo(target_index, QAbstractItemView.PositionAtCenter)
            return True
        return False

    def restore_current_selection_after_sort(self):
        if not self.current_entry_id:
            return
        row = self.list_model.row_for_entry_id(self.current_entry_id)
        if row < 0:
            return
        target_index = self.list_model.index(row, 2)
        if target_index.isValid():
            scrollbar = self.list_view.verticalScrollBar()
            previous_scroll = scrollbar.value()
            selection_model = self.list_view.selectionModel()
            if selection_model:
                selection_model.blockSignals(True)
                selection_model.select(target_index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
                selection_model.setCurrentIndex(target_index, QItemSelectionModel.NoUpdate)
                selection_model.blockSignals(False)
            self.restore_scrollbar_value(previous_scroll)

    def restore_scrollbar_value(self, value):
        scrollbar = self.list_view.verticalScrollBar()
        target_value = max(scrollbar.minimum(), min(value, scrollbar.maximum()))
        scrollbar.setValue(target_value)
        QTimer.singleShot(0, lambda: scrollbar.setValue(max(scrollbar.minimum(), min(target_value, scrollbar.maximum()))))

    def apply_theme(self):
        safe_apply_mica(self, self._is_dark_mode)
        preview_palette = self.preview_panel.palette()
        if self._is_dark_mode:
            preview_palette.setColor(QPalette.Window, QColor("#202020"))
            self.preview_panel.setPalette(preview_palette)
            self.preview_panel.setStyleSheet("#previewPanel { background-color: #202020; }")
            self.setStyleSheet(
                """
                QMainWindow { background-color: #1e1e1e; color: #ffffff; }
                QWidget { color: #ffffff; }
                QPushButton {
                    background-color: #303030;
                    color: #ffffff;
                    border: 1px solid #3f3f3f;
                    border-radius: 6px;
                    padding: 8px 14px;
                }
                QPushButton:hover { background-color: #3a3a3a; }
                QPushButton:checked { background-color: #0078d4; border-color: #0078d4; }
                QSplitter::handle {
                    background-color: #252525;
                    border-left: 1px solid #3f3f3f;
                    border-right: 1px solid #151515;
                }
                QSplitter::handle:hover { background-color: #3a3a3a; }
                QTableView {
                    background-color: #2b2b2b;
                    alternate-background-color: #333333;
                    color: #ffffff;
                    border: 1px solid #3f3f3f;
                    selection-background-color: #004b8d;
                }
                QTableView QTableCornerButton::section,
                QAbstractScrollArea::corner {
                    background-color: #242424;
                    border: none;
                }
                QHeaderView {
                    background-color: #252525;
                }
                QHeaderView::section {
                    background-color: #252525;
                    color: #ffffff;
                    border: none;
                    border-bottom: 1px solid #3f3f3f;
                    padding: 6px;
                    font-weight: bold;
                }
                QTableCornerButton::section {
                    background-color: #252525;
                    border: none;
                    border-bottom: 1px solid #3f3f3f;
                    border-right: 1px solid #3f3f3f;
                }
                QScrollBar:vertical, QScrollBar:horizontal {
                    background-color: #242424;
                    border: none;
                    margin: 0;
                }
                QScrollBar:vertical { width: 14px; }
                QScrollBar:horizontal { height: 14px; }
                QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
                    background-color: #5a5a5a;
                    border-radius: 7px;
                    min-height: 28px;
                    min-width: 28px;
                }
                QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
                    background-color: #707070;
                }
                QScrollBar::add-line, QScrollBar::sub-line {
                    width: 0;
                    height: 0;
                    background: none;
                    border: none;
                }
                QScrollBar::add-page, QScrollBar::sub-page {
                    background: none;
                }
                QStatusBar {
                    background-color: #2b2b2b;
                    color: #ffffff;
                    border-top: 1px solid #3f3f3f;
                }
                QProgressBar {
                    border: 1px solid #3f3f3f;
                    border-radius: 3px;
                    background-color: #1e1e1e;
                    text-align: center;
                }
                QProgressBar::chunk { background-color: #0078d4; }
                """
            )
        else:
            preview_palette.setColor(QPalette.Window, QColor("#f6f6f6"))
            self.preview_panel.setPalette(preview_palette)
            self.preview_panel.setStyleSheet("#previewPanel { background-color: #f6f6f6; }")
            self.setStyleSheet(
                """
                QMainWindow { background-color: #ffffff; color: #000000; }
                QPushButton {
                    background-color: #f3f3f3;
                    color: #000000;
                    border: 1px solid #d9d9d9;
                    border-radius: 6px;
                    padding: 8px 14px;
                }
                QPushButton:hover { background-color: #ececec; }
                QPushButton:checked { background-color: #0078d4; color: #ffffff; border-color: #0078d4; }
                QSplitter::handle {
                    background-color: #f3f3f3;
                    border-left: 1px solid #d9d9d9;
                    border-right: 1px solid #ffffff;
                }
                QSplitter::handle:hover { background-color: #e5e5e5; }
                QTableView {
                    background-color: #ffffff;
                    alternate-background-color: #f7f7f7;
                    color: #000000;
                    border: 1px solid #d9d9d9;
                    selection-background-color: #cce4ff;
                }
                QHeaderView::section {
                    background-color: #f3f3f3;
                    color: #000000;
                    border: none;
                    border-bottom: 1px solid #d9d9d9;
                    padding: 6px;
                    font-weight: bold;
                }
                QStatusBar {
                    background-color: #f9f9f9;
                    color: #000000;
                    border-top: 1px solid #d9d9d9;
                }
                QProgressBar {
                    border: 1px solid #d9d9d9;
                    border-radius: 3px;
                    background-color: #ffffff;
                    text-align: center;
                }
                QProgressBar::chunk { background-color: #0078d4; }
                """
            )
        self.preview_panel.style().unpolish(self.preview_panel)
        self.preview_panel.style().polish(self.preview_panel)
        self.preview_panel.update()

    def show_settings(self):
        previous_settings = self.settings_manager.current_settings.copy()
        dialog = SettingsWindow(self.settings_manager, self)
        if dialog.exec_() != QDialog.Accepted:
            return

        settings = self.settings_manager.current_settings
        self._is_dark_mode = theme_is_dark(settings["dark_mode"])
        self.process_button.setText(settings["operation_mode"].capitalize())
        self.thumbnail_delegate.set_thumbnail_size(settings["thumbnail_size"])
        self.list_view.verticalHeader().setDefaultSectionSize(settings["thumbnail_size"] + 8)
        self.setup_table_headers()
        self.apply_theme()

        thumb_changed = settings["thumbnail_size"] != previous_settings["thumbnail_size"]
        raw_preview_changed = settings["raw_preview_quality"] != previous_settings["raw_preview_quality"]
        grouping_changed = settings["auto_select_pairs"] != previous_settings["auto_select_pairs"]
        subfolders_changed = settings["include_subfolders"] != previous_settings["include_subfolders"]
        theme_changed = settings["dark_mode"] != previous_settings["dark_mode"]

        if raw_preview_changed:
            self.raw_cache.clear()

        if self.current_folder and (thumb_changed or grouping_changed or subfolders_changed):
            self.load_image_files(self.current_folder)
        elif theme_changed:
            self.list_view.viewport().update()
            if self.current_entry():
                self.load_preview_image(choose_preview_path(self.current_entry(), self.show_raw))

    def select_folder(self):
        if self.file_operation_active:
            return
        folder_path = QFileDialog.getExistingDirectory(self, "Select Image Folder", self.current_folder or "")
        if not folder_path:
            return
        self.current_folder = folder_path
        self.remember_source_folder(folder_path)
        self.current_image_path = None
        self.current_entry_id = None
        self.preview_label.setText("No image selected")
        self.preview_label.setPixmap(QPixmap())
        self.load_image_files(folder_path)

    def remember_source_folder(self, folder_path):
        if not folder_path:
            return
        settings = self.settings_manager.current_settings.copy()
        if settings.get("default_source_folder") == folder_path:
            return
        settings["default_source_folder"] = folder_path
        self.settings_manager.save_settings(settings)

    def set_loading_state(self, active):
        self.loading_in_progress = active
        self.status_progress.setVisible(active)
        if active:
            self.select_folder_button.setText("Cancel")
            try:
                self.select_folder_button.clicked.disconnect()
            except TypeError:
                pass
            self.select_folder_button.clicked.connect(self.cancel_loading)
        else:
            self.select_folder_button.setText("Select Folder")
            try:
                self.select_folder_button.clicked.disconnect()
            except TypeError:
                pass
            self.select_folder_button.clicked.connect(self.select_folder)

    def cancel_loading(self):
        if self.loader_thread and self.loader_thread.isRunning():
            self.loader_thread.cancel()
            self.statusBar().showMessage("Cancelling image load...", 3000)

    def _is_current_loader(self):
        """Return True when the emitting loader thread is still the active one.

        Signals from a cancelled loader stay queued in the GUI event loop, so
        without this check entries from the previous folder could be inserted
        into the newly loaded model.
        """
        sender = self.sender()
        return sender is None or sender is self.loader_thread

    def load_image_files(self, directory):
        if not directory or not os.path.isdir(directory):
            return
        self._initial_load_started = True

        if self.loader_thread and self.loader_thread.isRunning():
            self.loader_thread.cancel()
            if not self.loader_thread.wait(5000):
                LOGGER.warning("Previous image loader did not stop in time")
        if self.metadata_thread and self.metadata_thread.isRunning():
            self.metadata_thread.cancel()
            if not self.metadata_thread.wait(5000):
                LOGGER.warning("Previous metadata loader did not stop in time")

        self.preview_request_id += 1
        self.current_entry_id = None
        self.current_image_path = None
        self.preview_label.setText("Loading...")
        self.preview_label.setPixmap(QPixmap())
        self.list_model.defer_sort = True
        self.list_model.clear()
        self.setup_table_headers()
        self.status_progress.setRange(0, 0)
        self.set_loading_state(True)

        self.metadata_thread = MetadataLoaderThread(self.settings_manager.get_setting("thumbnail_size"))
        self.metadata_thread.metadata_ready_signal.connect(self.update_entry_metadata)
        self.metadata_thread.start()

        self.loader_thread = ImageLoaderThread(
            directory,
            self.settings_manager.get_setting("include_subfolders"),
            self.settings_manager.get_setting("auto_select_pairs"),
        )
        self.loader_thread.progress_signal.connect(self.update_load_progress)
        self.loader_thread.status_signal.connect(self.statusBar().showMessage)
        self.loader_thread.entries_loaded_signal.connect(self.add_entries_to_model)
        self.loader_thread.finished_signal.connect(self.loading_finished)
        self.loader_thread.start()

    def get_cached_exif(self, file_path):
        cache_key = normalize_path(file_path)
        if cache_key in self.exif_cache:
            self.exif_cache.move_to_end(cache_key)
            return self.exif_cache[cache_key]
        exif_data = extract_exif(file_path)
        self.exif_cache[cache_key] = exif_data
        trim_cache(self.exif_cache, MAX_EXIF_CACHE)
        return exif_data

    def add_entries_to_model(self, entries):
        if not self._is_current_loader():
            return
        if self.loader_thread and self.loader_thread.cancelled:
            return
        self.list_model.add_entries(entries, apply_sort=False)
        if self.metadata_thread and self.metadata_thread.isRunning():
            self.metadata_thread.add_entries(self.visible_entries(), priority=True)
        if self.list_model.rowCount() and not self.list_view.selectionModel().selectedRows():
            self.list_view.selectRow(0)

    def update_entry_metadata(self, entry_id, exif_data, thumbnail_image):
        row = self.list_model.row_for_entry_id(entry_id)
        entry = self.list_model.entry_at(row) if row >= 0 else None
        if entry:
            preview_path = choose_preview_path(entry, show_raw=False)
            if preview_path:
                self.exif_cache[normalize_path(preview_path)] = exif_data or {}
                trim_cache(self.exif_cache, MAX_EXIF_CACHE)
        self.list_model.update_entry_metadata(entry_id, exif_data, thumbnail_image)

    def visible_entries(self, extra_rows=20):
        if not self.list_model.rowCount():
            return []
        top_index = self.list_view.indexAt(QPoint(0, 0))
        bottom_index = self.list_view.indexAt(QPoint(0, max(self.list_view.viewport().height() - 1, 0)))
        start_row = top_index.row() if top_index.isValid() else 0
        end_row = bottom_index.row() if bottom_index.isValid() else min(self.list_model.rowCount() - 1, start_row + 30)
        start_row = max(0, start_row - extra_rows)
        end_row = min(self.list_model.rowCount() - 1, end_row + extra_rows)
        return [self.list_model.entry_at(row) for row in range(start_row, end_row + 1) if self.list_model.entry_at(row)]

    def queue_visible_metadata(self):
        if self.metadata_thread and self.metadata_thread.isRunning():
            self.metadata_thread.add_entries(self.visible_entries(), priority=True)

    def loading_finished(self):
        if not self._is_current_loader():
            return
        cancelled = bool(self.loader_thread and self.loader_thread.cancelled)
        self.list_model.defer_sort = False
        self.set_loading_state(False)
        self.status_progress.setVisible(False)
        self.status_progress.setRange(0, 1)
        self.status_progress.setValue(0)
        if cancelled and self.metadata_thread and self.metadata_thread.isRunning():
            self.metadata_thread.cancel()
        if self.list_model.rowCount():
            self.list_model.apply_current_sort()
        if cancelled:
            self.statusBar().showMessage(f"Cancelled after loading {self.list_model.rowCount()} entries", 3000)
        else:
            self.statusBar().showMessage(f"Loaded {self.list_model.rowCount()} entries", 3000)
        if self.list_model.rowCount():
            self.list_view.selectRow(0)
        elif not cancelled:
            self.preview_label.setText("No images found")
        else:
            self.preview_label.setText("Loading cancelled")
        if not cancelled:
            self.queue_visible_metadata()

    def update_load_progress(self, current, total):
        if not self._is_current_loader():
            return
        if total <= 0:
            self.status_progress.setRange(0, 0)
            return
        self.status_progress.setRange(0, max(total, 1))
        self.status_progress.setValue(current)

    def current_entry(self):
        selected_rows = self.list_view.selectionModel().selectedRows()
        if not selected_rows:
            return None
        row = selected_rows[0].row()
        return self.list_model.entry_at(row)

    def display_selected_image(self, selected, deselected=None):
        indexes = selected.indexes()
        if not indexes:
            return
        row = indexes[0].row()
        entry = self.list_model.entry_at(row)
        if not entry:
            return
        self.current_entry_id = entry["entry_id"]
        if self.metadata_thread and self.metadata_thread.isRunning():
            nearby = [self.list_model.entry_at(candidate) for candidate in range(max(0, row - 2), min(self.list_model.rowCount(), row + 3))]
            self.metadata_thread.add_entries([item for item in nearby if item], priority=True)
        file_path = choose_preview_path(entry, self.show_raw)
        if not file_path:
            self.preview_label.setText("No preview available")
            self.preview_label.setPixmap(QPixmap())
            self.current_image_path = None
            return
        self.current_image_path = file_path
        self.load_preview_image(file_path)
        self.prefetch_nearby_previews(row)

    def prefetch_nearby_previews(self, row):
        if not self.metadata_thread or not self.metadata_thread.isRunning():
            return
        nearby = []
        for candidate in (row + 1, row - 1):
            entry = self.list_model.entry_at(candidate)
            if entry:
                nearby.append(entry)
        self.metadata_thread.add_entries(nearby, priority=True)

    def preview_target_size(self):
        size = self.preview_label.size()
        if size.width() <= 0 or size.height() <= 0:
            return QSize(800, 600)
        return size

    def start_raw_preview_thread(self, file_path, quality):
        self.preview_request_id += 1
        request_id = self.preview_request_id
        gamma_value = self.settings_manager.get_setting("raw_gamma")
        thread = RawPreviewThread(request_id, file_path, self.preview_target_size(), quality, gamma_value)
        self._register_preview_thread(thread)
        thread.finished_signal.connect(self.handle_raw_preview_ready)
        thread.error_signal.connect(self.handle_raw_preview_error)
        thread.finished.connect(self.cleanup_preview_thread)
        thread.start()

    def start_image_preview_thread(self, file_path, high_quality=True):
        self.preview_request_id += 1
        request_id = self.preview_request_id
        thread = ImagePreviewThread(request_id, file_path, self.preview_target_size(), high_quality)
        self._register_preview_thread(thread)
        thread.finished_signal.connect(self.handle_image_preview_ready)
        thread.error_signal.connect(self.handle_image_preview_error)
        thread.finished.connect(self.cleanup_preview_thread)
        thread.start()

    def _register_preview_thread(self, thread):
        # Keep a Python reference until the thread has actually finished;
        # otherwise a cancelled-but-running QThread can be garbage collected
        # and crash the process.
        for old_thread in list(self.preview_threads):
            if old_thread.isRunning():
                old_thread.cancel()
        self.preview_threads.add(thread)
        self.preview_thread = thread

    def show_preview_loading(self, text):
        pixmap = self.preview_label.pixmap()
        if pixmap is None or pixmap.isNull():
            self.preview_label.setText(text)

    def load_preview_image(self, file_path, low_quality=False):
        if not file_path or not os.path.exists(file_path):
            self.preview_label.setText("Missing file")
            self.preview_label.setPixmap(QPixmap())
            return

        self.current_image_path = file_path
        if is_raw_file(file_path):
            quality = "low" if low_quality else self.settings_manager.get_setting("raw_preview_quality")
            target_size = self.preview_target_size()
            cache_key = (
                normalize_path(file_path),
                target_size.width(),
                target_size.height(),
                quality,
                float(self.settings_manager.get_setting("raw_gamma")),
            )
            if cache_key in self.raw_cache:
                self.raw_cache.move_to_end(cache_key)
                self.set_preview_pixmap(self.raw_cache[cache_key])
                return
            self.show_preview_loading("Loading RAW image...")
            self.start_raw_preview_thread(file_path, quality)
            return

        self.show_preview_loading("Loading image...")
        self.start_image_preview_thread(file_path, high_quality=not low_quality)

    def set_preview_pixmap(self, scaled_pixmap):
        self.preview_label.setText("")
        self.preview_label.setPixmap(scaled_pixmap)

    def handle_raw_preview_ready(self, request_id, file_path, scaled_image):
        if request_id != self.preview_request_id or file_path != self.current_image_path:
            return
        scaled_pixmap = QPixmap.fromImage(scaled_image)
        thread = self.sender()
        target_size = thread.target_size if thread is not None else self.preview_target_size()
        quality = thread.quality if thread is not None else self.settings_manager.get_setting("raw_preview_quality")
        gamma = float(thread.gamma if thread is not None else self.settings_manager.get_setting("raw_gamma"))
        cache_key = (
            normalize_path(file_path),
            target_size.width(),
            target_size.height(),
            quality,
            gamma,
        )
        self.raw_cache[cache_key] = scaled_pixmap
        trim_cache(self.raw_cache, MAX_RAW_CACHE)
        self.set_preview_pixmap(scaled_pixmap)

    def handle_raw_preview_error(self, request_id, file_path, error_text):
        if request_id != self.preview_request_id or file_path != self.current_image_path:
            return
        self.preview_label.setText(f"Error loading RAW image:\n{error_text}")
        self.preview_label.setPixmap(QPixmap())

    def handle_image_preview_ready(self, request_id, file_path, scaled_image):
        if request_id != self.preview_request_id or file_path != self.current_image_path:
            return
        self.set_preview_pixmap(QPixmap.fromImage(scaled_image))

    def handle_image_preview_error(self, request_id, file_path, error_text):
        if request_id != self.preview_request_id or file_path != self.current_image_path:
            return
        LOGGER.warning("Could not load preview for %s: %s", file_path, error_text)
        self.preview_label.setText(f"Could not load image\n{Path(file_path).name}")
        self.preview_label.setPixmap(QPixmap())

    def cleanup_preview_thread(self):
        thread = self.sender()
        if thread is None:
            return
        self.preview_threads.discard(thread)
        if thread is self.preview_thread:
            self.preview_thread = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resize_timer.start(150)

    def reload_preview_after_resize(self):
        if self.current_image_path:
            self.load_preview_image(self.current_image_path, low_quality=True)

    def toggle_preview_mode(self):
        self.show_raw = self.toggle_button.isChecked()
        self.toggle_button.setText("Show JPG" if self.show_raw else "Show RAW")
        entry = self.current_entry()
        if entry:
            self.load_preview_image(choose_preview_path(entry, self.show_raw))

    def handle_model_changed(self, top_left, bottom_right, roles=None):
        if top_left.column() != 0 or bottom_right.column() != 0:
            return
        checked_count = len(self.list_model.checked_entry_ids)
        self.statusBar().showMessage(f"Selected {checked_count} entries", 2000)

    def select_all_files(self):
        self.list_model.set_all_checked(True)

    def select_none_files(self):
        self.list_model.set_all_checked(False)

    def get_selected_rows(self):
        return self.list_model.selected_entries()

    def get_exif_datetime_filename(self, file_path, exif_data=None):
        metadata = exif_data or self.get_cached_exif(file_path)
        return exif_filename_from_metadata(
            metadata,
            Path(file_path).stem,
            self.settings_manager.get_setting("rename_format"),
        )

    def next_available_path(self, destination_path):
        destination = Path(destination_path)
        counter = 1
        while destination.exists():
            destination = destination.with_name(f"{destination.stem}_{counter}{destination.suffix}")
            counter += 1
        return destination

    def resolve_destination_path(self, source_path, destination_folder, rename_files, exif_data, replace_all, skip_all):
        source = Path(source_path)
        if rename_files:
            base_name = self.get_exif_datetime_filename(str(source), exif_data)
            target_path = Path(destination_folder) / f"{base_name}{source.suffix}"
            if paths_are_same(source, target_path) or target_path.exists():
                target_path = self.next_available_path(target_path)
            return str(target_path), replace_all, skip_all, False

        target_path = Path(destination_folder) / source.name
        if paths_are_same(source, target_path):
            # Copying into the source folder itself must never offer to
            # replace (and delete) the original file.
            return str(self.next_available_path(target_path)), replace_all, skip_all, False
        if not target_path.exists():
            return str(target_path), replace_all, skip_all, False
        if replace_all:
            return str(target_path), replace_all, skip_all, True
        if skip_all:
            return None, replace_all, skip_all, False

        dialog = Win11MessageBox(
            self,
            "File Exists",
            f"File {target_path.name} already exists.\nChoose Yes to replace, No to keep both versions.",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.YesToAll | QMessageBox.NoToAll,
            QMessageBox.Question,
        )
        response = dialog.exec_()
        if response == QMessageBox.YesToAll:
            return str(target_path), True, skip_all, True
        if response == QMessageBox.Yes:
            return str(target_path), replace_all, skip_all, True
        if response == QMessageBox.NoToAll:
            return None, replace_all, True, False
        if response == QMessageBox.Rejected:
            return None, replace_all, skip_all, False
        unique_path = self.next_available_path(target_path)
        return str(unique_path), replace_all, skip_all, False

    def copy_file_safely(self, source_path, target_path, replace):
        source = Path(source_path)
        target = Path(target_path)
        if paths_are_same(source, target):
            raise ValueError("Source and destination are the same file")
        target.parent.mkdir(parents=True, exist_ok=True)

        if not replace and not target.exists():
            shutil.copy2(source, target)
            return

        # Copy next to the target first and swap atomically, so a failed or
        # interrupted copy never destroys the file that is being replaced.
        temp_path = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        try:
            shutil.copy2(source, temp_path)
            os.replace(temp_path, target)
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    LOGGER.warning("Could not remove temporary file %s", temp_path)

    def move_file_safely(self, source_path, target_path, replace):
        source = Path(source_path)
        target = Path(target_path)
        if paths_are_same(source, target):
            raise ValueError("Source and destination are the same file")
        target.parent.mkdir(parents=True, exist_ok=True)

        if replace or not target.exists():
            try:
                os.replace(source, target)
                return
            except OSError:
                # Different volume or a locked target: fall back to copy+delete.
                pass

        self.copy_file_safely(source, target, True)
        try:
            source.unlink()
        except OSError:
            LOGGER.warning("Copied %s but could not remove the source file", source)

    def set_operation_state(self, enabled):
        for button in self.buttons:
            button.setEnabled(enabled)

    def process_selected_files(self):
        if self.file_operation_active:
            return
        selected_rows = self.get_selected_rows()
        if not selected_rows:
            QMessageBox.warning(self, "No Selection", "Please select files to process.")
            return

        destination = self.settings_manager.get_setting("destination_folder")
        operation_mode = self.settings_manager.get_setting("operation_mode")
        rename_files = self.settings_manager.get_setting("rename_files")

        if not destination:
            QMessageBox.warning(self, "No Destination", "Please set a destination folder in Settings.")
            return
        if not os.path.isdir(destination):
            QMessageBox.warning(self, "Invalid Destination", "The destination folder does not exist.")
            return
        if operation_mode == "move" and paths_are_same(destination, self.current_folder):
            Win11MessageBox(
                self,
                "Same Folder",
                "The destination folder is the same as the source folder.\n"
                "Choose a different destination before moving files.",
                QMessageBox.Ok,
                QMessageBox.Warning,
            ).exec_()
            return

        total_files = sum(len(entry.get("all_paths", [])) for _, entry in selected_rows)
        if not total_files:
            QMessageBox.warning(self, "No Files", "The selected entries do not contain any files.")
            return

        self.file_operation_active = True
        self.set_operation_state(False)
        progress = FileOperationDialog(self, operation_mode, total_files)
        progress.show()
        QApplication.processEvents()

        replace_all = False
        skip_all = False
        processed_count = 0
        success_count = 0
        failed_count = 0
        skipped_count = 0
        moved_anything = False
        cancelled = False

        try:
            for _, entry in selected_rows:
                if cancelled:
                    break
                entry_exif = entry.get("exif") or {}
                if rename_files and not entry_exif.get("datetime_original"):
                    metadata_path = choose_preview_path(entry, show_raw=False)
                    if metadata_path:
                        entry_exif = self.get_cached_exif(metadata_path)
                for file_path in entry.get("all_paths", []):
                    if progress.is_cancelled():
                        cancelled = True
                        break

                    processed_count += 1
                    progress.update_status(processed_count, total_files, Path(file_path).name)
                    QApplication.processEvents()

                    target_path, replace_all, skip_all, should_replace = self.resolve_destination_path(
                        file_path,
                        destination,
                        rename_files,
                        entry_exif,
                        replace_all,
                        skip_all,
                    )
                    if not target_path:
                        skipped_count += 1
                        continue

                    try:
                        if operation_mode == "copy":
                            self.copy_file_safely(file_path, target_path, should_replace)
                        else:
                            self.move_file_safely(file_path, target_path, should_replace)
                            moved_anything = True
                        success_count += 1
                    except Exception:
                        failed_count += 1
                        LOGGER.warning("Failed while %sing %s", operation_mode, file_path, exc_info=True)
                        Win11MessageBox(
                            self,
                            "Error",
                            f"Error while {operation_mode}ing {Path(file_path).name}. See log for details.",
                            QMessageBox.Ok,
                            QMessageBox.Critical,
                        ).exec_()
        finally:
            progress.close()
            self.file_operation_active = False
            self.set_operation_state(True)

        if moved_anything:
            self.load_image_files(self.current_folder)

        operation_past = "copied" if operation_mode == "copy" else "moved"
        if cancelled:
            summary = f"Cancelled after {success_count} of {total_files} file(s)."
        else:
            summary = f"{success_count} file(s) {operation_past} to {destination}."
        if skipped_count:
            summary += f"\n{skipped_count} file(s) skipped."
        if failed_count:
            summary += f"\n{failed_count} file(s) failed. See log for details."
        Win11MessageBox(
            self,
            "Complete" if not cancelled else "Cancelled",
            summary,
            QMessageBox.Ok,
            QMessageBox.Information if not failed_count else QMessageBox.Warning,
        ).exec_()

    def edit_current_image(self):
        if not self.current_image_path:
            QMessageBox.warning(self, "No Image", "Please select an image to edit.")
            return

        editor_path = self.settings_manager.get_setting("editor_path")
        work_dir = self.settings_manager.get_setting("edit_directory")

        if not editor_path or not os.path.exists(editor_path):
            QMessageBox.warning(self, "No Editor", "Please configure an external editor in Settings.")
            return
        if not work_dir:
            QMessageBox.warning(self, "No Edit Directory", "Please configure an edit directory in Settings.")
            return

        try:
            os.makedirs(work_dir, exist_ok=True)
            current_path = Path(self.current_image_path)
            entry = self.current_entry() or {"exif": self.get_cached_exif(self.current_image_path)}
            base_name = self.get_exif_datetime_filename(self.current_image_path, entry.get("exif")) or current_path.stem
            work_copy = Path(work_dir) / f"{base_name}_edit{current_path.suffix}"
            created_new_copy = False

            if work_copy.exists():
                dialog = Win11MessageBox(
                    self,
                    "File Exists",
                    "An edited version already exists.\nChoose Yes to edit the existing file or No to create a new version.",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Question,
                )
                if len(dialog.buttons) >= 2:
                    dialog.buttons[0].setText("Edit Existing")
                    dialog.buttons[1].setText("Create New")
                response = dialog.exec_()
                if response == QMessageBox.No:
                    work_copy = self.next_available_path(work_copy)
                    shutil.copy2(self.current_image_path, work_copy)
                    created_new_copy = True
            else:
                shutil.copy2(self.current_image_path, work_copy)
                created_new_copy = True

            try:
                subprocess.Popen([editor_path, str(work_copy)])
            except Exception:
                LOGGER.exception("Failed to launch editor")
                if created_new_copy and work_copy.exists():
                    work_copy.unlink()
                QMessageBox.critical(self, "Error", "Failed to launch the external editor.")
        except Exception:
            LOGGER.exception("Failed to prepare file for editing")
            QMessageBox.critical(self, "Error", "Failed to prepare the file for editing.")

    def closeEvent(self, event):
        if self.file_operation_active:
            event.ignore()
            self.statusBar().showMessage("A file operation is still running. Please wait.", 3000)
            return

        self._closing = True
        threads = []
        if self.loader_thread:
            self.loader_thread.cancel()
            threads.append(("image loader", self.loader_thread))
        if self.metadata_thread:
            self.metadata_thread.cancel()
            threads.append(("metadata loader", self.metadata_thread))
        for thread in list(self.preview_threads):
            thread.cancel()
            threads.append(("preview", thread))

        for name, thread in threads:
            if thread.isRunning() and not thread.wait(8000):
                LOGGER.warning("The %s thread did not stop before shutdown", name)

        super().closeEvent(event)


def install_exception_hook():
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        LOGGER.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))

    sys.excepthook = handle_exception


def main():
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    ensure_application_identity()
    install_exception_hook()
    app = QApplication(sys.argv)
    app.setApplicationDisplayName(APP_NAME)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
