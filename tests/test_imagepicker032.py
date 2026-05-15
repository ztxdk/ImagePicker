import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

import ImagePicker032 as mod


class ImagePicker032Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.project_dir = Path(__file__).resolve().parents[1]
        cls.pics_dir = cls.project_dir / "pics"

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

    def test_choose_preview_path_prefers_raw_when_requested(self):
        entry = {
            "jpg_path": "image.jpg",
            "raw_path": "image.arw",
            "single_path": None,
        }

        self.assertEqual(mod.choose_preview_path(entry, False), "image.jpg")
        self.assertEqual(mod.choose_preview_path(entry, True), "image.arw")

    def test_exif_filename_falls_back_without_datetime(self):
        exif_data = {"datetime_original": ""}
        result = mod.exif_filename_from_metadata(exif_data, "fallback_name", "YYYYMMDD-HHMMSS")
        self.assertEqual(result, "fallback_name")

    def test_scan_image_files_finds_project_samples(self):
        records = mod.scan_image_files(str(self.pics_dir), include_subfolders=False)
        self.assertGreater(len(records), 0)
        self.assertTrue(any(item["display_name"].lower().endswith(".jpg") for item in records))

    def test_main_window_smoke_loads_rows(self):
        original_get_dir = mod.QFileDialog.getExistingDirectory
        mod.QFileDialog.getExistingDirectory = staticmethod(lambda *args, **kwargs: str(self.pics_dir))
        try:
            window = mod.MainWindow()
            window.load_image_files(str(self.pics_dir))
            window.loader_thread.wait(30000)
            self.app.processEvents()

            self.assertGreater(window.list_model.rowCount(), 0)
            self.assertEqual(len(window.list_view.selectionModel().selectedRows()), 1)
            window.close()
        finally:
            mod.QFileDialog.getExistingDirectory = original_get_dir

    def test_loader_cancel_stops_before_processing_all_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            for index in range(80):
                (temp_path / f"image_{index:03}.jpg").write_bytes(b"not a real jpg")

            thread = mod.ImageLoaderThread(str(temp_path), False, False)
            loaded = []
            thread.entries_loaded_signal.connect(lambda entries: loaded.extend(entries))
            thread.start()

            while not loaded and thread.isRunning():
                self.app.processEvents()
                time.sleep(0.005)

            thread.cancel()
            thread.wait(5000)
            self.app.processEvents()

            self.assertTrue(thread.cancelled)
            self.assertLessEqual(len(loaded), 80)

    def test_table_model_tracks_selection_without_standard_items(self):
        model = mod.ImageTableModel()
        model.add_entries([
            {
                "entry_id": "one",
                "display_name": "one.jpg",
                "jpg_path": "one.jpg",
                "raw_path": None,
                "single_path": None,
                "all_paths": ["one.jpg"],
                "exif": {},
                "thumbnail": mod.QPixmap(),
            }
        ])

        index = model.index(0, 0)
        self.assertEqual(model.data(index, mod.Qt.CheckStateRole), mod.Qt.Unchecked)
        self.assertTrue(model.setData(index, mod.Qt.Checked, mod.Qt.CheckStateRole))
        self.assertEqual(len(model.selected_entries()), 1)


if __name__ == "__main__":
    unittest.main()
