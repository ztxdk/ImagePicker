# Image Picker

Image Picker is a desktop photo culling tool for quickly reviewing a folder of images and selecting the files you want to keep, copy, move, or edit externally.

The current development version is `ImagePicker035.py`.

This project is vibe coded: it was built iteratively with AI assistance and practical testing, with the goal of making the photo selection workflow fast and useful.

## Features

- Fast folder loading for large photo sets.
- Progressive metadata and thumbnail loading so the UI becomes usable quickly.
- Sortable columns: filename (natural order), EXIF date, shutter speed, ISO, focal length, and lens.
- RAW/JPG pairing, with paired files shown as one row when enabled.
- JPG and RAW preview support, both decoded off the UI thread.
- Mouse wheel navigation over the preview image.
- `Space` or middle mouse click toggles selection for the current image.
- Copy or move selected images to a destination folder, with safe overwrite handling.
- Optional EXIF-based renaming using `YYYYMMDD-HHMMSS`.
- Optional subfolder scanning.
- Remembers the last opened folder between runs.
- Light, dark, or automatic theme mode.
- Resizable table/preview layout using the splitter between them.
- External editor integration, including configurable edit working directory.

## Supported File Types

Image Picker scans common image formats including:

- JPG/JPEG
- RAW formats such as ARW, RAW, CR2, NEF, and DNG
- PNG, TIFF, BMP, WebP, and HEIC

RAW support depends on `rawpy` and the camera format being supported by LibRaw.

HEIC files are listed and can be selected, copied, or moved, but Qt can only preview them when a HEIF codec is installed (on Windows that is the HEIF Image Extension from the Microsoft Store). Without the codec the preview pane explains how to enable it.

## Requirements

- Windows is the primary target platform.
- Python 3.13 and Python 3.14 both work with the pinned dependency versions.
- Python packages listed in `requirements.txt`:

```text
PyQt5==5.15.11
darkdetect==0.8.0
ExifRead==3.5.1
numpy==2.5.4
rawpy==0.28.0
win32mica==3.0; platform_system == "Windows"
```

## Setup

Create a virtual environment and install dependencies:

```powershell
py -3.13 -m venv .venv313
.\.venv313\Scripts\python.exe -m pip install --upgrade pip
.\.venv313\Scripts\python.exe -m pip install -r requirements.txt
```

## Running

Run the latest version:

```powershell
.\.venv313\Scripts\python.exe .\ImagePicker035.py
```

Older version files are kept in local history and Git history, but only `ImagePicker035.py` is kept as the active Python source file in the repository.

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

Settings and logs are stored in the application config directory returned by Qt's `QStandardPaths.AppConfigLocation` (`%LOCALAPPDATA%\ImagePicker` on Windows), with a fallback to `~/.image_picker/ImagePicker`. Older settings files next to the script or in the previous `Image Picker\ImagePicker` folder are still read as a fallback, and the log file rotates at 2 MB with three backups.

## Performance Notes

Version 035 is optimized for one-off photo culling sessions on new folders. It intentionally does not use a persistent cache between application runs.

Important performance choices:

- The initial scan walks folders with `os.scandir` and skips unreadable or symlinked directories instead of aborting.
- Rows are inserted into the table in batches while scanning when RAW/JPG pairing is disabled.
- EXIF and thumbnails are loaded asynchronously after rows appear.
- Visible and nearby rows are prioritized.
- Selected/current images are prioritized over background work.
- JPG preview and thumbnails use `QImageReader.setScaledSize()` to avoid decoding more image data than needed.
- Both JPG and RAW previews are decoded in worker threads, so scrolling and resizing stay responsive.
- Row lookups use an `entry_id` index instead of scanning the whole list for every metadata update.
- Thumbnails live in a bounded LRU cache (32 MB budget, adjusted to the thumbnail size) instead of one pixmap per file, so memory stays flat for folders with tens of thousands of images.
- Copy and move run in a worker thread. Destination conflicts are resolved on the UI thread first, then the file transfer happens without blocking the window.

## Safety Notes

- Copying or moving into the source folder can never overwrite or delete the original file.
- Replacing an existing destination file copies to a temporary file first and swaps it in atomically, so a failed copy does not destroy the old file.
- Moving into the folder that is already loaded is refused, because it would delete the files you are looking at.
- Worker threads are cancelled cooperatively; the app no longer calls `QThread.terminate()`.
- Closing the window during a copy/move cancels the operation and closes once it has stopped safely.

## Tests

The test suite uses the offscreen Qt platform, so it runs without opening windows:

```powershell
.\.venv313\Scripts\python.exe -m unittest discover -s tests -p "test_imagepicker035.py"
```

`tests/test_imagepicker035.py` covers folder scanning, RAW/JPG pairing, table model sorting, same-folder copy/move safety, atomic replacement, the file operation thread, stale loader signals, the dark-mode settings dialog and the settings fallback.

Older version test files may still exist locally; they are excluded from the command above because they exercise historical versions of the app.

## Building a Windows executable

```powershell
.\.venv313\Scripts\python.exe -m pip install pyinstaller
.\.venv313\Scripts\pyinstaller.exe --noconfirm ImagePicker035.spec
```

The one-file executable is written to `dist/`. `ImagePicker035.spec` bundles `icons/` and the application icon.

## Project Files

- `ImagePicker035.py`: Current application version.
- `tests/test_imagepicker035.py`: Test suite for the current version.
- `ImagePicker035.spec`: PyInstaller build definition.
- `requirements.txt`: Runtime dependencies (pinned).
- `icons/`: UI icons used by the application.
- `agents.md`: Development/task log for this project.
- `TODO.md`: Historical prioritized technical TODO list.
