import ctypes
import datetime
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
from collections import OrderedDict, deque
from pathlib import Path

import darkdetect
import exifread
import numpy as np
import rawpy
from PyQt5.QtCore import QAbstractTableModel, QEvent, QItemSelectionModel, QModelIndex, QPoint, QRect, QSize, Qt, QThread, QTimer, QStandardPaths, pyqtSignal
from PyQt5.QtGui import (
    QColor,
    QIcon,
    QImage,
    QImageReader,
    QPalette,
    QPen,
    QPixmap,
    QPolygon,
    QTransform,
)
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
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
    QSizePolicy,
    QSplitter,
    QSpinBox,
    QStyledItemDelegate,
    QStyle,
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
APP_VERSION = "0.34"
APP_DISPLAY_NAME = f"{APP_NAME} - v{APP_VERSION} (2026-07-05)"
RAW_EXTENSIONS = {".arw", ".raw", ".cr2", ".nef", ".dng"}
JPEG_EXTENSIONS = {".jpg", ".jpeg"}
IMAGE_EXTENSIONS = JPEG_EXTENSIONS | RAW_EXTENSIONS | {".png", ".tif", ".tiff", ".bmp", ".webp", ".heic"}
MAX_EXIF_CACHE = 4096
MAX_THUMB_CACHE = 1024
MAX_RAW_CACHE = 16


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
    base_dir = QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation)
    if not base_dir:
        base_dir = str(Path.home() / ".image_picker")
    base_path = Path(base_dir)
    if base_path.name.lower() != "imagepicker":
        base_path = base_path / "ImagePicker"
    base_path.mkdir(parents=True, exist_ok=True)
    return base_path


def setup_logging():
    log_dir = get_app_config_dir()
    log_file = log_dir / "image_picker.log"
    logger = logging.getLogger("image_picker")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


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
    return str(Path(value).resolve()).lower()


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


def extract_orientation(tags):
    orientation = tags.get("Image Orientation")
    if not orientation:
        return 1
    try:
        return int(orientation.values[0])
    except Exception:
        return 1


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
        "iso": "",
        "focal_length": "",
        "lens_model": "",
        "datetime_original": "",
        "orientation": 1,
    }
    try:
        with open(file_path, "rb") as file_handle:
            tags = exifread.process_file(file_handle, details=False)

        datetime_text = exif_text(tags.get("EXIF DateTimeOriginal") or tags.get("Image DateTime"))
        dt_obj = parse_exif_datetime(datetime_text)

        data = defaults.copy()
        data["datetime_original"] = datetime_text
        data["orientation"] = extract_orientation(tags)
        if dt_obj:
            data["date"] = dt_obj.strftime("%d-%m-%y")
            data["time"] = dt_obj.strftime("%H:%M:%S")

        data["f_stop"] = format_f_number(tags.get("EXIF FNumber"))
        data["exposure"] = exif_text(tags.get("EXIF ExposureTime"))
        data["iso"] = exif_text(tags.get("EXIF ISOSpeedRatings"))
        data["focal_length"] = format_focal_length(tags.get("EXIF FocalLength"))
        data["lens_model"] = exif_text(tags.get("EXIF LensModel") or tags.get("Image Model"))
        return data
    except Exception:
        LOGGER.exception("Error extracting EXIF data from %s", file_path)
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


def orientation_transform(orientation_value):
    transform = QTransform()
    if orientation_value == 2:
        transform.scale(-1, 1)
    elif orientation_value == 3:
        transform.rotate(180)
    elif orientation_value == 4:
        transform.scale(1, -1)
    elif orientation_value == 5:
        transform.scale(-1, 1)
        transform.rotate(270)
    elif orientation_value == 6:
        transform.rotate(90)
    elif orientation_value == 7:
        transform.scale(-1, 1)
        transform.rotate(90)
    elif orientation_value == 8:
        transform.rotate(270)
    return transform


def apply_orientation(pixmap, orientation_value):
    if not pixmap or pixmap.isNull() or orientation_value in (None, 1):
        return pixmap
    return pixmap.transformed(orientation_transform(orientation_value), Qt.SmoothTransformation)


def scaled_pixmap_for_label(pixmap, target_size, smooth=True):
    if not pixmap or pixmap.isNull():
        return QPixmap()
    width = max(target_size.width(), 1)
    height = max(target_size.height(), 1)
    transform_mode = Qt.SmoothTransformation if smooth else Qt.FastTransformation
    return pixmap.scaled(width, height, Qt.KeepAspectRatio, transform_mode)


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
        LOGGER.exception("Error creating thumbnail for %s", image_path)
        return QImage()


def create_thumbnail(image_path, thumbnail_size, exif_data=None):
    try:
        image = create_thumbnail_image(image_path, thumbnail_size)
        if image.isNull():
            return QPixmap()
        return QPixmap.fromImage(image)
    except Exception:
        LOGGER.exception("Error creating thumbnail for %s", image_path)
        return QPixmap()


def scan_image_files(directory, include_subfolders, should_continue=None):
    directory_path = Path(directory)
    file_records = []
    if include_subfolders:
        iterator = directory_path.rglob("*")
    else:
        iterator = directory_path.iterdir()

    for path in iterator:
        if should_continue is not None and not should_continue():
            break
        if not path.is_file():
            continue
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        try:
            display_name = str(path.relative_to(directory_path))
        except ValueError:
            display_name = path.name
        file_records.append(
            {
                "full_path": str(path),
                "display_name": display_name.replace("\\", "/"),
                "suffix": path.suffix.lower(),
                "group_key": str(path.relative_to(directory_path).with_suffix("")).replace("\\", "/").lower(),
                "modified_time": path.stat().st_mtime,
            }
        )
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
        "thumbnail": QPixmap(),
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
                "thumbnail": QPixmap(),
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

    def _read_json_file(self, file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as file_handle:
                return json.load(file_handle)
        except Exception:
            LOGGER.exception("Error loading settings from %s", file_path)
            return {}

    def load_settings(self):
        settings = self.default_settings.copy()
        if self.settings_file.exists():
            settings.update(self._read_json_file(self.settings_file))
            return settings
        if self.legacy_settings_file.exists():
            settings.update(self._read_json_file(self.legacy_settings_file))
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
        self.setFixedSize(460, 640)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(12, 16, 12, 16)

        self.create_settings_controls(layout)

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

        settings = {
            "default_source_folder": self.source_input.text().strip(),
            "destination_folder": self.dest_input.text().strip(),
            "operation_mode": "move" if self.move_radio.isChecked() else "copy",
            "dark_mode": dark_mode,
            "thumbnail_size": self.thumb_spin.value(),
            "raw_preview_quality": self.raw_combo.currentText().lower(),
            "raw_gamma": self.gamma_spin.value(),
            "auto_select_pairs": self.pair_checkbox.isChecked(),
            "rename_files": self.rename_checkbox.isChecked(),
            "rename_format": "YYYYMMDD-HHMMSS",
            "include_subfolders": self.subfolder_checkbox.isChecked(),
            "editor_path": self.editor_path_input.text().strip(),
            "edit_directory": self.work_dir_input.text().strip(),
            "save_edits_format": "jpg",
            "editor_presets": [],
        }
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
        self.message_label = QLabel(message)
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

        button_layout = QHBoxLayout()
        self.buttons = []
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
                button_layout.addWidget(button)
        layout.addLayout(button_layout)

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
    headers = [" ", "Thumbnail", "Filename", "Date", "Time", "F-Stop", "EV", "ISO", "FL", "Lens"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries = []
        self.checked_entry_ids = set()
        self.sort_column = 2
        self.sort_order = Qt.AscendingOrder

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
            return Qt.Checked if entry["entry_id"] in self.checked_entry_ids else Qt.Unchecked
        if column == 1 and role == Qt.DecorationRole:
            return entry.get("thumbnail") or QPixmap()
        if role in (Qt.DisplayRole, Qt.EditRole):
            exif_data = entry.get("exif") or {}
            values = {
                2: entry.get("display_name", ""),
                3: exif_data.get("date", ""),
                4: exif_data.get("time", ""),
                5: exif_data.get("f_stop", ""),
                6: exif_data.get("exposure", ""),
                7: exif_data.get("iso", ""),
                8: exif_data.get("focal_length", ""),
                9: exif_data.get("lens_model", ""),
            }
            return values.get(column, "")
        if role == Qt.UserRole + 1:
            return entry
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid() or index.column() != 0 or role != Qt.CheckStateRole:
            return False
        entry_id = self.entries[index.row()]["entry_id"]
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
        self.endResetModel()

    def add_entries(self, entries):
        if not entries:
            return
        entries = sorted(entries, key=self.sort_key, reverse=self.sort_order == Qt.DescendingOrder)
        start_row = len(self.entries)
        end_row = start_row + len(entries) - 1
        self.beginInsertRows(QModelIndex(), start_row, end_row)
        self.entries.extend(entries)
        self.endInsertRows()
        self.apply_current_sort()

    def entry_at(self, row):
        if 0 <= row < len(self.entries):
            return self.entries[row]
        return None

    def row_for_entry_id(self, entry_id):
        for row, entry in enumerate(self.entries):
            if entry.get("entry_id") == entry_id:
                return row
        return -1

    def update_entry_metadata(self, entry_id, exif_data, thumbnail_image):
        row = self.row_for_entry_id(entry_id)
        if row < 0:
            return
        entry = self.entries[row]
        entry["exif"] = exif_data or {}
        if thumbnail_image and not thumbnail_image.isNull():
            entry["thumbnail"] = QPixmap.fromImage(thumbnail_image)
        if self.sort_column in (3, 4, 5, 6, 7, 8, 9):
            if self.apply_current_sort():
                return
        top_left = self.index(row, 1)
        bottom_right = self.index(row, len(self.headers) - 1)
        self.dataChanged.emit(top_left, bottom_right, [Qt.DisplayRole, Qt.DecorationRole])

    def set_all_checked(self, checked):
        if checked:
            self.checked_entry_ids = {entry["entry_id"] for entry in self.entries}
        else:
            self.checked_entry_ids.clear()
        if self.entries:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.entries) - 1, 0), [Qt.CheckStateRole])

    def selected_entries(self):
        return [(row, entry) for row, entry in enumerate(self.entries) if entry["entry_id"] in self.checked_entry_ids]

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
        self.layoutChanged.emit()
        return True

    def sort_key(self, entry):
        exif_data = entry.get("exif") or {}
        column = self.sort_column
        if column == 0:
            return (entry["entry_id"] not in self.checked_entry_ids, self.filename_key(entry))
        if column == 1:
            thumbnail = entry.get("thumbnail")
            has_thumbnail = bool(thumbnail is not None and not thumbnail.isNull())
            return (not has_thumbnail, self.filename_key(entry))
        if column == 2:
            return self.filename_key(entry)
        if column in (3, 4):
            return self.datetime_key(entry, exif_data)
        if column in (5, 6, 7, 8):
            value_map = {
                5: exif_data.get("f_stop", ""),
                6: exif_data.get("exposure", ""),
                7: exif_data.get("iso", ""),
                8: exif_data.get("focal_length", ""),
            }
            number = parse_leading_number(value_map[column])
            return (number is None, number if number is not None else 0, self.filename_key(entry))
        if column == 9:
            return (str(exif_data.get("lens_model", "")).casefold(), self.filename_key(entry))
        return self.filename_key(entry)

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
        if not pixmap or pixmap.isNull():
            super().paint(painter, option, index)
            return

        x_pos = option.rect.x() + (option.rect.width() - self.thumbnail_size.width()) // 2
        y_pos = option.rect.y() + (option.rect.height() - self.thumbnail_size.height()) // 2
        painter.drawPixmap(x_pos, y_pos, self.thumbnail_size.width(), self.thumbnail_size.height(), pixmap)

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

    def build_entries(self):
        file_records = scan_image_files(self.directory, self.include_subfolders, lambda: self.running)
        if self.combine_pairs:
            return build_grouped_entries(file_records)
        return [build_single_entry(record) for record in file_records]

    def run(self):
        try:
            entries = self.build_entries()
            if not self.running:
                return
            total_entries = len(entries)
            batch = []
            for index, entry in enumerate(entries, start=1):
                if not self.running:
                    break
                batch.append(entry)
                if len(batch) >= 64:
                    self.entries_loaded_signal.emit(batch)
                    batch = []
                self.progress_signal.emit(index, total_entries)
                if index == 1 or index % 100 == 0:
                    self.status_signal.emit(f"Found {index} of {total_entries} entries")
            if batch and self.running:
                self.entries_loaded_signal.emit(batch)
        except Exception:
            LOGGER.exception("Image loader thread failed")
        finally:
            self.finished_signal.emit()


class MetadataLoaderThread(QThread):
    metadata_ready_signal = pyqtSignal(str, dict, QImage)
    status_signal = pyqtSignal(str)

    def __init__(self, thumbnail_size):
        super().__init__()
        self.thumbnail_size = thumbnail_size
        self.running = True
        self.pending = deque()
        self.pending_ids = set()
        self.completed_ids = set()

    def cancel(self):
        self.running = False

    def add_entries(self, entries, priority=False):
        new_entries = []
        for entry in entries:
            entry_id = entry.get("entry_id")
            if not entry_id or entry_id in self.pending_ids or entry_id in self.completed_ids:
                continue
            self.pending_ids.add(entry_id)
            new_entries.append(entry)
        if priority:
            for entry in reversed(new_entries):
                self.pending.appendleft(entry)
        else:
            self.pending.extend(new_entries)

    def run(self):
        while self.running:
            if not self.pending:
                self.msleep(10)
                continue
            entry = self.pending.popleft()
            entry_id = entry.get("entry_id")
            self.pending_ids.discard(entry_id)
            if not self.running:
                break
            preview_path = choose_preview_path(entry, show_raw=False)
            if not preview_path:
                self.completed_ids.add(entry_id)
                self.metadata_ready_signal.emit(entry_id, {}, QImage())
                continue
            exif_data = extract_exif(preview_path)
            if not self.running:
                break
            thumbnail = create_thumbnail_image(preview_path, self.thumbnail_size)
            if not self.running:
                break
            self.completed_ids.add(entry_id)
            self.metadata_ready_signal.emit(entry_id, exif_data, thumbnail)


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
        self.preview_request_id = 0
        self.preview_thread = None
        self.loader_thread = None
        self.metadata_thread = None
        self.exif_cache = OrderedDict()
        self.thumbnail_cache = OrderedDict()
        self.raw_cache = OrderedDict()
        self._initial_load_started = False

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

        if thumb_changed:
            self.thumbnail_cache.clear()
        if raw_preview_changed:
            self.raw_cache.clear()

        if self.current_folder and (thumb_changed or grouping_changed or subfolders_changed):
            self.load_image_files(self.current_folder)
        elif theme_changed:
            self.list_view.viewport().update()
            if self.current_entry():
                self.load_preview_image(choose_preview_path(self.current_entry(), self.show_raw))

    def select_folder(self):
        folder_path = QFileDialog.getExistingDirectory(self, "Select Image Folder", self.current_folder or "")
        if not folder_path:
            return
        self.current_folder = folder_path
        self.current_image_path = None
        self.current_entry_id = None
        self.preview_label.setText("No image selected")
        self.preview_label.setPixmap(QPixmap())
        self.load_image_files(folder_path)

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

    def load_image_files(self, directory):
        if not directory or not os.path.isdir(directory):
            return
        self._initial_load_started = True

        if self.loader_thread and self.loader_thread.isRunning():
            self.loader_thread.cancel()
            self.loader_thread.wait(3000)
        if self.metadata_thread and self.metadata_thread.isRunning():
            self.metadata_thread.cancel()
            if not self.metadata_thread.wait(3000):
                self.metadata_thread.terminate()
                self.metadata_thread.wait(1000)

        self.current_entry_id = None
        self.current_image_path = None
        self.preview_label.setText("Loading...")
        self.preview_label.setPixmap(QPixmap())
        self.list_model.clear()
        self.setup_table_headers()
        self.status_progress.setValue(0)
        self.status_progress.setMaximum(1)
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

    def get_cached_thumbnail(self, file_path, thumbnail_size, exif_data):
        cache_key = (normalize_path(file_path), int(thumbnail_size))
        if cache_key in self.thumbnail_cache:
            self.thumbnail_cache.move_to_end(cache_key)
            return self.thumbnail_cache[cache_key]
        thumbnail = create_thumbnail(file_path, thumbnail_size, exif_data)
        self.thumbnail_cache[cache_key] = thumbnail
        trim_cache(self.thumbnail_cache, MAX_THUMB_CACHE)
        return thumbnail

    def add_entries_to_model(self, entries):
        if self.loader_thread and self.loader_thread.cancelled:
            return
        self.list_model.add_entries(entries)
        if self.metadata_thread and self.metadata_thread.isRunning():
            self.metadata_thread.add_entries(self.visible_entries(), priority=True)
        if self.list_model.rowCount() and not self.list_view.selectionModel().selectedRows():
            self.list_view.selectRow(0)

    def update_entry_metadata(self, entry_id, exif_data, thumbnail_image):
        entry = None
        row = self.list_model.row_for_entry_id(entry_id)
        if row >= 0:
            entry = self.list_model.entry_at(row)
        if entry:
            preview_path = choose_preview_path(entry, show_raw=False)
            if preview_path:
                exif_key = normalize_path(preview_path)
                self.exif_cache[exif_key] = exif_data or {}
                if thumbnail_image and not thumbnail_image.isNull():
                    thumb_key = (exif_key, int(self.settings_manager.get_setting("thumbnail_size")))
                    self.thumbnail_cache[thumb_key] = QPixmap.fromImage(thumbnail_image)
                trim_cache(self.exif_cache, MAX_EXIF_CACHE)
                trim_cache(self.thumbnail_cache, MAX_THUMB_CACHE)
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
        cancelled = bool(self.loader_thread and self.loader_thread.cancelled)
        self.set_loading_state(False)
        self.status_progress.setVisible(False)
        if cancelled and self.metadata_thread and self.metadata_thread.isRunning():
            self.metadata_thread.cancel()
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
        self.status_progress.setMaximum(max(total, 1))
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
        if self.preview_thread and self.preview_thread.isRunning():
            self.preview_thread.cancel()
        self.preview_thread = thread
        thread.finished_signal.connect(self.handle_raw_preview_ready)
        thread.error_signal.connect(self.handle_raw_preview_error)
        thread.finished.connect(self.cleanup_preview_thread)
        thread.start()

    def load_preview_image(self, file_path, low_quality=False):
        if not file_path or not os.path.exists(file_path):
            self.preview_label.setText("Missing file")
            self.preview_label.setPixmap(QPixmap())
            return

        self.current_image_path = file_path
        if is_raw_file(file_path):
            quality = "low" if low_quality else self.settings_manager.get_setting("raw_preview_quality")
            cache_key = (
                normalize_path(file_path),
                self.preview_target_size().width(),
                self.preview_target_size().height(),
                quality,
                float(self.settings_manager.get_setting("raw_gamma")),
            )
            if cache_key in self.raw_cache:
                self.raw_cache.move_to_end(cache_key)
                self.set_preview_pixmap(self.raw_cache[cache_key])
                return
            self.preview_label.setText("Loading RAW image...")
            self.preview_label.setPixmap(QPixmap())
            self.start_raw_preview_thread(file_path, quality)
            return

        try:
            target_size = self.preview_target_size()
            reader = QImageReader(file_path)
            reader.setAutoTransform(True)
            source_size = reader.size()
            if source_size.isValid():
                source_size.scale(target_size, Qt.KeepAspectRatio)
                reader.setScaledSize(source_size)
            image = reader.read()
            if image.isNull():
                self.preview_label.setText("Could not load image")
                self.preview_label.setPixmap(QPixmap())
                return
            scaled = scaled_image_for_size(image, target_size, smooth=not low_quality)
            self.set_preview_pixmap(QPixmap.fromImage(scaled))
        except Exception:
            LOGGER.exception("Error loading preview for %s", file_path)
            self.preview_label.setText("Error loading preview")
            self.preview_label.setPixmap(QPixmap())

    def set_preview_pixmap(self, scaled_pixmap):
        self.preview_label.setText("")
        self.preview_label.setPixmap(scaled_pixmap)

    def handle_raw_preview_ready(self, request_id, file_path, scaled_image):
        if request_id != self.preview_request_id or file_path != self.current_image_path:
            return
        scaled_pixmap = QPixmap.fromImage(scaled_image)
        cache_key = (
            normalize_path(file_path),
            self.preview_target_size().width(),
            self.preview_target_size().height(),
            self.settings_manager.get_setting("raw_preview_quality"),
            float(self.settings_manager.get_setting("raw_gamma")),
        )
        self.raw_cache[cache_key] = scaled_pixmap
        trim_cache(self.raw_cache, MAX_RAW_CACHE)
        self.set_preview_pixmap(scaled_pixmap)

    def handle_raw_preview_error(self, request_id, file_path, error_text):
        if request_id != self.preview_request_id or file_path != self.current_image_path:
            return
        self.preview_label.setText(f"Error loading RAW image:\n{error_text}")
        self.preview_label.setPixmap(QPixmap())

    def cleanup_preview_thread(self):
        sender = self.sender()
        if sender is self.preview_thread:
            self.preview_thread = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resize_timer.start(150)

    def reload_preview_after_resize(self):
        if self.current_image_path:
            self.load_preview_image(self.current_image_path, low_quality=is_raw_file(self.current_image_path))

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
            if target_path.exists():
                target_path = self.next_available_path(target_path)
            return str(target_path), replace_all, skip_all, False

        target_path = Path(destination_folder) / source.name
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
        unique_path = self.next_available_path(target_path)
        return str(unique_path), replace_all, skip_all, False

    def process_selected_files(self):
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

        total_files = sum(len(entry["all_paths"]) for _, entry in selected_rows)
        progress = FileOperationDialog(self, operation_mode, total_files)
        progress.show()
        QApplication.processEvents()

        replace_all = False
        skip_all = False
        processed_count = 0
        moved_anything = False

        try:
            for _, entry in selected_rows:
                entry_exif = entry.get("exif") or {}
                if rename_files and not entry_exif.get("datetime_original"):
                    metadata_path = choose_preview_path(entry, show_raw=False)
                    if metadata_path:
                        entry_exif = self.get_cached_exif(metadata_path)
                entry_success = True
                for file_path in entry["all_paths"]:
                    if progress.is_cancelled():
                        self.statusBar().showMessage("File operation cancelled", 3000)
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
                        entry_success = False
                        continue

                    try:
                        if should_replace and os.path.exists(target_path):
                            os.remove(target_path)
                        if operation_mode == "copy":
                            shutil.copy2(file_path, target_path)
                        else:
                            shutil.move(file_path, target_path)
                            moved_anything = True
                    except Exception:
                        entry_success = False
                        LOGGER.exception("Failed while %sing %s", operation_mode, file_path)
                        dialog = Win11MessageBox(
                            self,
                            "Error",
                            f"Error while {operation_mode}ing {Path(file_path).name}. See log for details.",
                            QMessageBox.Ok,
                            QMessageBox.Critical,
                        )
                        dialog.exec_()

                if progress.is_cancelled():
                    break
                if not entry_success:
                    LOGGER.warning("Entry processed with issues: %s", entry["display_name"])

            if moved_anything:
                self.load_image_files(self.current_folder)

            operation_past = "copied" if operation_mode == "copy" else "moved"
            dialog = Win11MessageBox(
                self,
                "Complete",
                f"Successfully {operation_past} up to {processed_count} file(s) to {destination}.",
                QMessageBox.Ok,
                QMessageBox.Information,
            )
            dialog.exec_()
        finally:
            progress.close()

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
        if self.loader_thread and self.loader_thread.isRunning():
            self.loader_thread.cancel()
            self.loader_thread.wait(5000)
        if self.metadata_thread and self.metadata_thread.isRunning():
            self.metadata_thread.cancel()
            if not self.metadata_thread.wait(5000):
                self.metadata_thread.terminate()
                self.metadata_thread.wait(1000)
        if self.preview_thread and self.preview_thread.isRunning():
            self.preview_thread.cancel()
            self.preview_thread.wait(5000)
        super().closeEvent(event)


def main():
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    QApplication.setApplicationName(APP_NAME)
    app = QApplication(sys.argv)
    window = MainWindow()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
