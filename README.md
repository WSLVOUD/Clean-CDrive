# Clean-CDrive

**A safe Windows C: drive cleaner in Python, with both CLI and GUI.**

Removes temporary files, browser caches, icon caches, recent items, Windows Update leftovers, error reports, recycle bin, old logs, and orphaned AppData folders from uninstalled programs — all without touching Documents, Desktop, or Downloads.

## Usage

```bash
Clean-CDrive.exe -d          # Preview only (safe)
Clean-CDrive.exe             # Basic cleanup
Clean-CDrive.exe -a          # Add orphan AppData cleanup
Clean-CDrive-GUI.exe         # Graphical interface
```

## Safety

- Only targets well-known temp and cache locations
- Orphan AppData detection uses registry + AppX + white-list + process-path + file-activity **5-layer verification**
- Orphan mode is **list-only by default**; use `-a/--aggressive` to delete
- Every operation has independent try-catch — a single failure won't stop the rest

## Build from source

```bash
pyinstaller --onefile --console --name "Clean-CDrive" clean_cdrive.py
pyinstaller --onefile --noconsole --name "Clean-CDrive-GUI" clean_cdrive_gui.py
```

Requires Python 3.10+ and PyInstaller. No third-party dependencies for runtime — uses only `os`, `shutil`, `subprocess`, `tkinter`, `winreg`, and other stdlib modules.
