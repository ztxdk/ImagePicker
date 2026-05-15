# Image Picker

Image Picker is a desktop photo culling tool for quickly reviewing a folder of images and selecting the files you want to keep, copy, move, or edit externally.

The current development version is `ImagePicker032.py`.

## Features

- Fast folder loading for large photo sets.
- Progressive metadata and thumbnail loading so the UI becomes usable quickly.
- RAW/JPG pairing, with paired files shown as one row when enabled.
- JPG and RAW preview support.
- Mouse wheel navigation over the preview image.
- `Space` or middle mouse click toggles selection for the current image.
- Copy or move selected images to a destination folder.
- Optional EXIF-based renaming using `YYYYMMDD-HHMMSS`.
- Optional subfolder scanning.
- Light, dark, or automatic theme mode.
- Resizable table/preview layout using the splitter between them.
- External editor integration, including configurable edit working directory.

## Supported File Types

Image Picker scans common image formats including:

- JPG/JPEG
- RAW formats such as ARW, RAW, CR2, NEF, and DNG
- PNG, TIFF, BMP, WebP, and HEIC

RAW support depends on `rawpy` and the camera format being supported by LibRaw.

## Requirements

- Windows is the primary target platform.
- Python 3.14 is recommended. Python 3.13 is also known to work.
- Python packages listed in `requirements.txt`:

```text
PyQt5
darkdetect
exifread
numpy
rawpy
win32mica; platform_system == "Windows"
```

## Setup

Create a virtual environment and install dependencies:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Running

Run the latest version:

```powershell
.\.venv\Scripts\python.exe .\ImagePicker032.py
```

Older version files are kept in the repository for history and comparison, but `ImagePicker032.py` is the current optimized version.

## Basic Workflow

1. Start the application.
2. Select a source folder, or configure a default source folder in `Settings`.
3. Review images using the table and preview pane.
4. Use the mouse wheel over the preview to move between images.
5. Press `Space`, click the checkbox, or middle-click the preview to select an image.
6. Choose `Copy` or `Move` in `Settings`.
7. Click `Copy` or `Move` to process selected files.

## Controls

- `Space`: Toggle selection for the current image.
- Middle mouse click on preview: Toggle selection for the current image.
- Mouse wheel over preview: Move to previous or next image.
- `Show RAW` / `Show JPG`: Toggle preview source when a RAW/JPG pair exists.
- Splitter between table and preview: Resize the table and image preview areas.

## Settings

The settings dialog includes:

- Default source folder.
- Destination folder.
- Copy or move operation mode.
- Theme: Auto, Light, or Dark.
- Thumbnail size.
- RAW preview quality.
- RAW gamma.
- Combine paired RAW/JPG files into one row.
- Rename files using EXIF date.
- Include subfolders when loading images.
- External editor path.
- Edit working directory.

Settings and logs are stored in the application config directory returned by Qt's `QStandardPaths.AppConfigLocation`, with a fallback to `~/.image_picker/ImagePicker`.

## Performance Notes

Version 032 is optimized for one-off photo culling sessions on new folders. It intentionally does not use a persistent cache between application runs.

Important performance choices:

- The initial scan only builds the file list and RAW/JPG grouping.
- Rows are inserted into the table in batches.
- EXIF and thumbnails are loaded asynchronously after rows appear.
- Visible and nearby rows are prioritized.
- Selected/current images are prioritized over background work.
- JPG preview and thumbnails use `QImageReader.setScaledSize()` to avoid decoding more image data than needed.
- Worker threads use `QImage`; `QPixmap` is created on the UI side.

## Testing

Run the test suite with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

Run only the latest version tests:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_imagepicker032
```

## Project Files

- `ImagePicker032.py`: Current optimized application version.
- `requirements.txt`: Runtime dependencies.
- `tests/test_imagepicker032.py`: Tests for the current version.
- `icons/`: UI icons used by the application.
- `agents.md`: Development/task log for this project.
- `TODO.md`: Historical prioritized technical TODO list.
