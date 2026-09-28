# NullLauncher

NullLauncher is a terminal Minecraft launcher for Windows 10/11. The source is split into modules, while release users get one self-contained `NullLauncher.exe` and do not need Python installed.

## Project layout

```text
NullLauncher.py                 # thin entry point
null_launcher/
  config.py                     # constants, translations, theme/runtime preferences
  utils.py                      # text, image/SIXEL, paths, proxy helpers
  updater.py                    # GitHub Release EXE updater
  state.py                      # persistent settings/accounts/proxies
  terminal.py                   # terminal input/rendering/menu/SIXEL drawing
  minecraft.py                  # minecraft-launcher-lib / Pillow bridge
  news.py                       # Minecraft.net news loading and translation
  catalog.py                    # version catalog/preload data
  app.py                        # application screens and launch flow
  diagnostics.py                # --diagnose
  cli.py                        # argument parsing and startup
scripts/build.ps1               # local Windows build
.github/workflows/build-windows.yml
NullLauncher.spec               # PyInstaller one-file build
```

## Development

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python NullLauncher.py
```

Diagnostics and version checks:

```powershell
python NullLauncher.py --diagnose
python NullLauncher.py --version
```

## Build a standalone EXE

On Windows run:

```powershell
.\scripts\build.ps1
```

The result is `dist\NullLauncher.exe`. It is a PyInstaller one-file executable with Python, `minecraft-launcher-lib`, and Pillow bundled, so end users do not install Python or pip dependencies.

The launcher remains a console application (`console=True`) because the terminal UI is the actual interface.

## GitHub Releases

The included Actions workflow builds on `windows-latest`. A manual workflow run uploads `NullLauncher.exe` as an Actions artifact. Pushing a tag such as `v1.10.1` also creates/updates the GitHub Release and attaches `dist/NullLauncher.exe`.

Before tagging a release, keep the version in `null_launcher/config.py` and `pyproject.toml` in sync.

## News image centering fix

Windows Terminal can report a different Win32 console-font size than the cell size it actually uses for SIXEL. The old code calculated the raster width from `GetCurrentConsoleFontEx`, so the launcher sometimes believed the picture occupied more columns than it really did and placed its origin too far left.

`Terminal.cell_pixel_size()` now prefers Windows Terminal's XTWINOPS `CSI 16 t` report (`CSI 6 ; height ; width t`). That metric is the same cell geometry Windows Terminal uses for SIXEL rendering. The result is cached per terminal grid size, and the legacy Win32/DPI path remains as a fallback.

## Auto-update behavior

Packaged builds look for `NullLauncher.exe` in the latest stable GitHub Release, verify GitHub's SHA-256 digest when available plus the PE `MZ` signature, download next to the running executable, then replace/restart it after the old process exits. Source checkouts deliberately do not self-update file-by-file; update those with Git.
