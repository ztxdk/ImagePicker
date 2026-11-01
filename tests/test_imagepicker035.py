import json
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

import ImagePicker035 as mod


PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


class ImagePicker035Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.project_dir = Path(__file__).resolve().parents[1]
        cls.pics_dir = cls.project_dir / "pics"
        mod.LOGGER.setLevel("ERROR")

    # --- helpers ---------------------------------------------------------

    def make_entry(self, name, datetime_original="", exposure_seconds=None, thumbnail=None):
        return {
            "entry_id": name,
            "display_name": name,
            "jpg_path": name,
            "raw_path": None,
            "single_path": None,
            "all_paths": [name],
            "modified_time": 0,
            "exif": {
                "datetime_original": datetime_original,
                "exposure_seconds": exposure_seconds,
            },
            "thumbnail": thumbnail,
        }

    def make_image_dir(self, directory, names):
        for name in names:
            (Path(directory) / name).write_bytes(PNG_BYTES)

    def wait_until(self, predicate, timeout=20.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        return False

    def run_window(self):
        original_get_dir = mod.QFileDialog.getExistingDirectory
        mod.QFileDialog.getExistingDirectory = staticmethod(lambda *args, **kwargs: str(self.pics_dir))
        try:
            window = mod.MainWindow()
        finally:
            mod.QFileDialog.getExistingDirectory = original_get_dir
        window._initial_load_started = True
        return window

    # --- existing behaviour ---------------------------------------------

    def test_build_grouped_entries_merges_raw_and_jpg(self):
        records = [
            {
                "full_path": str(self.pics_dir / "_DSC0001.JPG"),
                "display_name": "_DSC0001.JPG",
                "suffix": ".jpg",
                "group_key": "_dsc0001",
            },
            {
                "full_path": str(self.pics_dir / "_DSC0001.ARW"),
                "display_name": "_DSC0001.ARW",
                "suffix": ".arw",
                "group_key": "_dsc0001",
            },
        ]

        entries = mod.build_grouped_entries(records)

        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0]["jpg_path"].lower().endswith(".jpg"))
        self.assertTrue(entries[0]["raw_path"].lower().endswith(".arw"))
        self.assertEqual(len(entries[0]["all_paths"]), 2)
        # Thumbnails must not be created in the worker thread.
        self.assertIsNone(entries[0]["thumbnail"])

    def test_build_single_entry_tracks_single_path(self):
        record = {
            "full_path": str(self.pics_dir / "IMG_20250322_125652132.jpg"),
            "display_name": "IMG_20250322_125652132.jpg",
            "suffix": ".jpg",
            "group_key": "img_20250322_125652132",
        }

        entry = mod.build_single_entry(record)

        self.assertIsNotNone(entry["jpg_path"])
        self.assertIsNone(entry["raw_path"])
        self.assertEqual(entry["all_paths"], [record["full_path"]])
        self.assertIsNone(entry["thumbnail"])

    def test_choose_preview_path_prefers_raw_when_requested(self):
        entry = {"jpg_path": "image.jpg", "raw_path": "image.arw", "single_path": None}

        self.assertEqual(mod.choose_preview_path(entry, False), "image.jpg")
        self.assertEqual(mod.choose_preview_path(entry, True), "image.arw")

    def test_exif_filename_falls_back_without_datetime(self):
        exif_data = {"datetime_original": ""}
        result = mod.exif_filename_from_metadata(exif_data, "fallback_name", "YYYYMMDD-HHMMSS")
        self.assertEqual(result, "fallback_name")

    def test_exif_filename_uses_metadata_datetime(self):
        exif_data = {"datetime_original": "2024:03:04 05:06:07"}
        result = mod.exif_filename_from_metadata(exif_data, "fallback", "YYYYMMDD-HHMMSS")
        self.assertEqual(result, "20240304-050607")

    def test_scan_image_files_finds_project_samples(self):
        records = mod.scan_image_files(str(self.pics_dir), include_subfolders=False)
        self.assertGreater(len(records), 0)
        self.assertTrue(any(item["display_name"].lower().endswith(".jpg") for item in records))

    def test_iter_image_files_respects_subfolder_setting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            nested = root / "nested"
            nested.mkdir()
            self.make_image_dir(root, ["root.jpg"])
            self.make_image_dir(nested, ["nested.jpg"])

            flat = list(mod.iter_image_files(str(root), include_subfolders=False))
            deep = list(mod.iter_image_files(str(root), include_subfolders=True))

        self.assertEqual([record["display_name"] for record in flat], ["root.jpg"])
        self.assertEqual(
            sorted(record["display_name"] for record in deep),
            ["nested/nested.jpg", "root.jpg"],
        )

    # --- loader ----------------------------------------------------------

    def test_loader_cancelled_before_start_emits_nothing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.make_image_dir(temp_dir, [f"image_{index:03}.jpg" for index in range(80)])
            thread = mod.ImageLoaderThread(temp_dir, False, False)
            loaded = []
            thread.entries_loaded_signal.connect(lambda entries: loaded.extend(entries))
            thread.cancel()
            thread.start()
            self.assertTrue(thread.wait(5000))
            self.app.processEvents()

        self.assertTrue(thread.cancelled)
        self.assertEqual(loaded, [])

    def test_second_load_ignores_signals_from_the_first_folder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first_dir = Path(temp_dir) / "first"
            second_dir = Path(temp_dir) / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            self.make_image_dir(first_dir, [f"first_{index:03}.jpg" for index in range(80)])
            self.make_image_dir(second_dir, ["second_001.jpg"])

            window = self.run_window()
            try:
                window.load_image_files(str(first_dir))
                window.load_image_files(str(second_dir))
                self.assertTrue(
                    self.wait_until(lambda: window.loader_thread is not None and not window.loader_thread.isRunning())
                )
                self.wait_until(lambda: window.list_model.rowCount() > 0)

                paths = [path for entry in window.list_model.entries for path in entry.get("all_paths", [])]
                self.assertTrue(paths)
                self.assertTrue(all(mod.normalize_path(path).startswith(mod.normalize_path(second_dir)) for path in paths))
            finally:
                window.close()

    def test_smoke_load_fills_rows_and_selection(self):
        window = self.run_window()
        try:
            window.load_image_files(str(self.pics_dir))
            self.assertTrue(self.wait_until(lambda: window.loader_thread is not None and not window.loader_thread.isRunning()))
            self.assertTrue(self.wait_until(lambda: window.list_model.rowCount() > 0))

            self.assertGreater(window.list_model.rowCount(), 0)
            self.assertEqual(len(window.list_view.selectionModel().selectedRows()), 1)
        finally:
            window.close()

    # --- model -----------------------------------------------------------

    def test_table_model_tracks_selection(self):
        model = mod.ImageTableModel()
        model.add_entries([self.make_entry("one.jpg")])

        index = model.index(0, 0)
        self.assertEqual(model.data(index, mod.Qt.CheckStateRole), mod.Qt.Unchecked)
        self.assertTrue(model.setData(index, mod.Qt.Checked, mod.Qt.CheckStateRole))
        self.assertEqual(len(model.selected_entries()), 1)

    def test_table_model_handles_missing_thumbnail(self):
        model = mod.ImageTableModel()
        model.add_entries([self.make_entry("one.jpg")])
        self.assertIsNone(model.data(model.index(0, 1), mod.Qt.DecorationRole))

    def test_metadata_is_stored_without_per_entry_pixmaps(self):
        model = mod.ImageTableModel()
        model.add_entries([self.make_entry("one.jpg")])
        image = mod.QImage(8, 8, mod.QImage.Format_RGB888)
        image.fill(mod.QColor("#808080"))

        model.update_entry_metadata("one.jpg", {"iso": "100"}, image)

        self.assertIsNone(model.entry_at(0)["thumbnail"])
        self.assertIsNotNone(model.thumbnail_for("one.jpg"))
        self.assertEqual(model.data(model.index(0, 7), mod.Qt.DisplayRole), "100")

    def test_thumbnail_cache_is_bounded(self):
        model = mod.ImageTableModel()
        model.set_thumbnail_limit(64)
        model.add_entries([self.make_entry(f"image_{index:03}.jpg") for index in range(120)])
        pixmap = mod.QPixmap(4, 4)

        for index in range(120):
            model.set_thumbnail(f"image_{index:03}.jpg", pixmap)

        self.assertLessEqual(len(model.thumbnails), 64)
        self.assertIsNotNone(model.thumbnail_for("image_119.jpg"))
        self.assertIsNone(model.thumbnail_for("image_000.jpg"))

    def test_table_model_sorts_filenames_naturally(self):
        model = mod.ImageTableModel()
        model.add_entries([
            self.make_entry("image_10.jpg"),
            self.make_entry("image_2.jpg"),
            self.make_entry("image_1.jpg"),
        ])

        model.sort(2, mod.Qt.AscendingOrder)

        self.assertEqual(
            [model.entry_at(row)["display_name"] for row in range(model.rowCount())],
            ["image_1.jpg", "image_2.jpg", "image_10.jpg"],
        )

    def test_table_model_sorts_by_exif_datetime_and_keeps_checked_entries(self):
        model = mod.ImageTableModel()
        model.add_entries([
            self.make_entry("late.jpg", "2025:01:02 10:00:00"),
            self.make_entry("early.jpg", "2024:12:31 10:00:00"),
        ])
        late_index = model.index(model.row_for_entry_id("late.jpg"), 0)
        self.assertTrue(model.setData(late_index, mod.Qt.Checked, mod.Qt.CheckStateRole))

        model.sort(3, mod.Qt.AscendingOrder)

        self.assertEqual(model.entry_at(0)["display_name"], "early.jpg")
        self.assertEqual(model.entry_at(1)["display_name"], "late.jpg")
        self.assertEqual([entry["display_name"] for _, entry in model.selected_entries()], ["late.jpg"])

    def test_table_model_sorts_shutter_speed_numerically(self):
        model = mod.ImageTableModel()
        model.add_entries([
            self.make_entry("slow.jpg", exposure_seconds=2.0),
            self.make_entry("fast.jpg", exposure_seconds=1 / 1000),
            self.make_entry("mid.jpg", exposure_seconds=1 / 250),
        ])

        model.sort(6, mod.Qt.AscendingOrder)

        self.assertEqual(
            [model.entry_at(row)["display_name"] for row in range(model.rowCount())],
            ["fast.jpg", "mid.jpg", "slow.jpg"],
        )

    def test_table_model_resorts_when_metadata_arrives_for_active_sort(self):
        model = mod.ImageTableModel()
        model.add_entries([
            self.make_entry("needs_metadata.jpg"),
            self.make_entry("already_early.jpg", "2024:01:01 09:00:00"),
        ])

        model.sort(3, mod.Qt.AscendingOrder)
        model.update_entry_metadata("needs_metadata.jpg", {"datetime_original": "2023:01:01 09:00:00"}, mod.QImage())

        self.assertEqual(model.entry_at(0)["display_name"], "needs_metadata.jpg")

    def test_row_index_stays_valid_after_sorting(self):
        model = mod.ImageTableModel()
        model.add_entries([self.make_entry(f"image_{index}.jpg") for index in range(20)])
        model.sort(2, mod.Qt.DescendingOrder)

        for row in range(model.rowCount()):
            entry = model.entry_at(row)
            self.assertEqual(model.row_for_entry_id(entry["entry_id"]), row)

    def test_sort_selection_restore_does_not_move_scrollbar(self):
        class RestoreHarness:
            restore_current_selection_after_sort = mod.MainWindow.restore_current_selection_after_sort
            restore_scrollbar_value = mod.MainWindow.restore_scrollbar_value

        harness = RestoreHarness()
        harness.list_view = mod.QTableView()
        harness.list_model = mod.ImageTableModel(harness.list_view)
        harness.list_view.setModel(harness.list_model)
        try:
            harness.list_model.add_entries([self.make_entry(f"image_{index:03}.jpg") for index in range(120)])
            harness.list_view.verticalHeader().setDefaultSectionSize(40)
            harness.list_view.resize(700, 260)
            harness.list_view.show()
            self.app.processEvents()

            harness.current_entry_id = "image_000.jpg"
            harness.list_view.verticalScrollBar().setValue(harness.list_view.verticalScrollBar().maximum())
            previous_scroll = harness.list_view.verticalScrollBar().value()

            harness.restore_current_selection_after_sort()
            self.app.processEvents()

            self.assertGreater(previous_scroll, 0)
            self.assertEqual(harness.list_view.verticalScrollBar().value(), previous_scroll)
        finally:
            harness.list_view.close()

    # --- exposure parsing ------------------------------------------------

    def test_parse_exposure_seconds(self):
        self.assertAlmostEqual(mod.parse_exposure_seconds("1/250"), 0.004)
        self.assertAlmostEqual(mod.parse_exposure_seconds("2"), 2.0)
        self.assertAlmostEqual(mod.parse_exposure_seconds("0.5s"), 0.5)
        self.assertIsNone(mod.parse_exposure_seconds(""))
        self.assertIsNone(mod.parse_exposure_seconds("1/0"))

    # --- file operation safety -------------------------------------------

    def test_paths_are_same_normalizes_case_and_relative_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "photo.jpg"
            file_path.write_bytes(b"x")
            self.assertTrue(mod.paths_are_same(file_path, f"{temp_dir}{os.sep}.{os.sep}photo.jpg"))
            self.assertTrue(mod.paths_are_same(file_path, str(file_path).upper()))
            self.assertFalse(mod.paths_are_same(file_path, file_path.with_name("other.jpg")))

    def test_copy_into_same_folder_keeps_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "photo.jpg"
            source.write_bytes(b"original")
            harness = FileOperationHarness()

            target_path, _, _, should_replace = harness.resolve_destination_path(
                str(source), temp_dir, False, {}, False, False
            )
            harness.copy_file_safely(str(source), target_path, should_replace)

            self.assertTrue(source.exists())
            self.assertEqual(source.read_bytes(), b"original")
            self.assertNotEqual(mod.normalize_path(target_path), mod.normalize_path(source))
            self.assertTrue(Path(target_path).exists())

    def test_copy_never_deletes_the_source_when_paths_are_equal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "photo.jpg"
            source.write_bytes(b"original")
            harness = FileOperationHarness()

            with self.assertRaises(ValueError):
                harness.copy_file_safely(str(source), str(source), True)
            self.assertTrue(source.exists())
            self.assertEqual(source.read_bytes(), b"original")

    def test_replacing_copy_keeps_the_original_until_the_copy_succeeds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.jpg"
            target = Path(temp_dir) / "target.jpg"
            source.write_bytes(b"new content")
            target.write_bytes(b"old content")
            harness = FileOperationHarness()

            harness.copy_file_safely(str(source), str(target), True)

            self.assertEqual(target.read_bytes(), b"new content")
            self.assertEqual(source.read_bytes(), b"new content")
            leftovers = [path.name for path in Path(temp_dir).iterdir() if path.name.endswith(".tmp")]
            self.assertEqual(leftovers, [])

    def test_move_never_deletes_the_source_when_paths_are_equal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "photo.jpg"
            source.write_bytes(b"original")
            harness = FileOperationHarness()

            with self.assertRaises(ValueError):
                harness.move_file_safely(str(source), str(source), True)
            self.assertTrue(source.exists())

    def test_move_to_another_folder_moves_the_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir) / "source"
            target_dir = Path(temp_dir) / "target"
            source_dir.mkdir()
            target_dir.mkdir()
            source = source_dir / "photo.jpg"
            source.write_bytes(b"content")
            harness = FileOperationHarness()

            target_path, _, _, _ = harness.resolve_destination_path(
                str(source), str(target_dir), False, {}, False, False
            )
            harness.move_file_safely(str(source), target_path, False)

            self.assertFalse(source.exists())
            self.assertEqual(Path(target_path).read_bytes(), b"content")

    def test_process_selected_files_warns_when_moving_into_the_source_folder(self):
        window = self.run_window()
        fake_dialogs = []
        original_dialog = mod.Win11MessageBox
        mod.Win11MessageBox = lambda *args, **kwargs: fake_dialogs.append(args[1]) or FakeDialog()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                self.make_image_dir(temp_dir, ["one.jpg"])
                window.current_folder = temp_dir
                window.load_image_files(temp_dir)
                self.assertTrue(self.wait_until(lambda: window.list_model.rowCount() > 0))
                window.list_model.set_all_checked(True)
                window.settings_manager.current_settings["destination_folder"] = temp_dir
                window.settings_manager.current_settings["operation_mode"] = "move"

                window.process_selected_files()

                self.assertTrue((Path(temp_dir) / "one.jpg").exists())
                self.assertIn("Same Folder", fake_dialogs)
        finally:
            mod.Win11MessageBox = original_dialog
            window.close()

    def test_process_selected_files_copies_to_destination(self):
        window = self.run_window()
        dialogs = []
        original_dialog = mod.Win11MessageBox
        mod.Win11MessageBox = lambda *args, **kwargs: dialogs.append(args[1]) or FakeDialog()
        try:
            with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
                self.make_image_dir(source_dir, ["a.jpg", "b.jpg"])
                window.current_folder = source_dir
                window.load_image_files(source_dir)
                self.assertTrue(self.wait_until(lambda: window.list_model.rowCount() == 2))
                window.list_model.set_all_checked(True)
                window.settings_manager.current_settings["destination_folder"] = target_dir
                window.settings_manager.current_settings["operation_mode"] = "copy"

                window.process_selected_files()
                self.assertTrue(self.wait_until(lambda: not window.file_operation_active))

                copied = sorted(path.name for path in Path(target_dir).iterdir())
                self.assertEqual(copied, ["a.jpg", "b.jpg"])
                self.assertIn("Complete", dialogs)
                self.assertTrue(all(button.isEnabled() for button in window.buttons))
        finally:
            mod.Win11MessageBox = original_dialog
            window.close()

    def test_process_selected_files_moves_files_and_reloads(self):
        window = self.run_window()
        dialogs = []
        original_dialog = mod.Win11MessageBox
        mod.Win11MessageBox = lambda *args, **kwargs: dialogs.append(args[1]) or FakeDialog()
        try:
            with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
                self.make_image_dir(source_dir, ["a.jpg", "b.jpg"])
                window.current_folder = source_dir
                window.load_image_files(source_dir)
                self.assertTrue(self.wait_until(lambda: window.list_model.rowCount() == 2))
                window.list_model.set_all_checked(True)
                window.settings_manager.current_settings["destination_folder"] = target_dir
                window.settings_manager.current_settings["operation_mode"] = "move"

                window.process_selected_files()
                self.assertTrue(self.wait_until(lambda: not window.file_operation_active))
                self.assertTrue(self.wait_until(lambda: not window.loading_in_progress))

                self.assertEqual(sorted(path.name for path in Path(target_dir).iterdir()), ["a.jpg", "b.jpg"])
                self.assertEqual(list(Path(source_dir).iterdir()), [])
                self.assertEqual(window.list_model.rowCount(), 0)
                self.assertIn("Complete", dialogs)
        finally:
            mod.Win11MessageBox = original_dialog
            window.close()

    def test_file_operation_thread_reports_completion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.jpg"
            target = Path(temp_dir) / "target.jpg"
            source.write_bytes(b"content")

            thread = mod.FileOperationThread([(str(source), str(target), False)], "copy")
            progress = []
            completed = []
            thread.progress_signal.connect(lambda index, total, name: progress.append((index, total, name)))
            thread.completed_signal.connect(lambda ok, failed, cancelled: completed.append((ok, failed, cancelled)))
            thread.start()
            self.assertTrue(thread.wait(10000))
            self.app.processEvents()

            self.assertEqual(completed, [(1, 0, False)])
            self.assertEqual(progress, [(1, 1, "source.jpg")])
            self.assertEqual(target.read_bytes(), b"content")

    def test_file_operation_thread_can_be_cancelled_before_start(self):
        jobs = [("a", "b", False), ("c", "d", False)]
        thread = mod.FileOperationThread(jobs, "move")
        completed = []
        thread.completed_signal.connect(lambda ok, failed, cancelled: completed.append((ok, failed, cancelled)))
        thread.cancel()
        thread.start()
        self.assertTrue(thread.wait(10000))
        self.app.processEvents()

        self.assertEqual(completed, [(0, 0, True)])

    def test_preview_error_message_explains_heic(self):
        self.assertIn("HEIC", mod.preview_error_message("photo.HEIC"))
        self.assertIn("boom", mod.preview_error_message("photo.jpg", "boom"))

    def test_metadata_thread_allows_refresh_of_completed_entries(self):
        thread = mod.MetadataLoaderThread(64)
        entry = self.make_entry("refresh.jpg")
        thread.start()
        try:
            thread.add_entries([entry])
            self.assertTrue(self.wait_until(lambda: "refresh.jpg" in thread._completed_ids))

            thread.add_entries([entry])
            with thread._condition:
                self.assertEqual(len(thread._pending), 0)

            thread.add_entries([entry], refresh=True)
            with thread._condition:
                pending_again = len(thread._pending) > 0 or "refresh.jpg" in thread._inflight_ids
            self.assertTrue(pending_again)
        finally:
            thread.cancel()
            thread.wait(5000)

    def test_settings_dialog_is_readable_in_dark_mode(self):
        window = self.run_window()
        try:
            window._is_dark_mode = True
            window.apply_theme()
            dialog = mod.SettingsWindow(window.settings_manager, window)
            try:
                dialog.show()
                self.app.processEvents()
                self.app.processEvents()

                image = dialog.grab().toImage()
                field_point = dialog.source_input.mapTo(dialog, mod.QPoint(6, 6))
                label_area = image.pixelColor(20, field_point.y())
                field_color = image.pixelColor(field_point)
                self.assertLess(sum(field_color.getRgb()[:3]), 200)
                self.assertLess(sum(label_area.getRgb()[:3]), 200)
            finally:
                dialog.close()
        finally:
            window.close()

    # --- settings ---------------------------------------------------------

    def test_log_and_settings_share_the_config_dir(self):
        config_dir = mod.get_app_config_dir()
        file_handlers = [
            handler for handler in mod.LOGGER.handlers
            if isinstance(handler, mod.logging.handlers.RotatingFileHandler)
        ]
        self.assertTrue(file_handlers)
        self.assertEqual(Path(file_handlers[0].baseFilename).parent, config_dir)

        manager = mod.SettingsManager()
        self.assertEqual(manager.settings_file.parent, config_dir)

    def test_settings_coercion_ignores_invalid_values(self):
        manager = mod.SettingsManager()
        coerced = manager._coerce_settings({
            "thumbnail_size": "not a number",
            "dark_mode": "yes",
            "operation_mode": 42,
            "unknown_key": "ignored",
        })

        self.assertNotIn("thumbnail_size", coerced)
        self.assertIs(coerced["dark_mode"], True)
        self.assertEqual(coerced["operation_mode"], "42")
        self.assertNotIn("unknown_key", coerced)

    def test_settings_round_trip_preserves_unknown_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_path.write_text(json.dumps({"rename_format": "YYYYMMDD-HHMM"}), encoding="utf-8")
            manager = mod.SettingsManager()
            original_file = manager.settings_file
            try:
                manager.settings_file = settings_path
                settings = manager.load_settings()
                self.assertEqual(settings["rename_format"], "YYYYMMDD-HHMM")
                self.assertEqual(settings["thumbnail_size"], 64)
            finally:
                manager.settings_file = original_file

    def test_saving_from_the_dialog_keeps_hidden_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = mod.SettingsManager()
            manager.settings_dir = Path(temp_dir)
            manager.settings_file = Path(temp_dir) / "settings.json"
            manager.current_settings["editor_presets"] = [{"name": "preset"}]
            manager.current_settings["rename_format"] = "YYYYMMDD-HHMM"
            with open(manager.settings_file, "w", encoding="utf-8") as file_handle:
                json.dump(manager.current_settings, file_handle)

            dialog = mod.SettingsWindow(manager, None)
            try:
                dialog.save_settings()
                saved = json.loads(manager.settings_file.read_text(encoding="utf-8"))
            finally:
                dialog.close()

            self.assertEqual(saved["editor_presets"], [{"name": "preset"}])
            self.assertEqual(saved["rename_format"], "YYYYMMDD-HHMM")


class FileOperationHarness:
    resolve_destination_path = mod.MainWindow.resolve_destination_path
    copy_file_safely = mod.MainWindow.copy_file_safely
    move_file_safely = mod.MainWindow.move_file_safely
    next_available_path = mod.MainWindow.next_available_path
    get_exif_datetime_filename = mod.MainWindow.get_exif_datetime_filename
    settings_manager = mod.SettingsManager()


class FakeDialog:
    def __init__(self, *args, **kwargs):
        self.buttons = []

    def exec_(self):
        return mod.QMessageBox.Ok

    def show(self):
        return None

    def close(self):
        return None


if __name__ == "__main__":
    unittest.main()
