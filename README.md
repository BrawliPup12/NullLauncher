# NullLauncher

![GitHub Release](https://img.shields.io/github/v/release/BrawliPup12/NullLauncher)
![GitHub Downloads](https://img.shields.io/github/downloads/BrawliPup12/NullLauncher/total)
![Platform](https://img.shields.io/badge/platform-Windows-blue)

A lightweight terminal-based Minecraft launcher for Windows.

NullLauncher is built around a simple idea:

> No unnecessary UI. No heavy framework. Just launch Minecraft.

It runs directly inside Windows Terminal / CMD and keeps the terminal-first workflow while providing version management, offline profiles, news, per-version settings, updates, diagnostics, and a small set of quality-of-life features.

---

## Disclaimer

NullLauncher is an independent project and is not affiliated with, endorsed by, or sponsored by Mojang Studios or Microsoft.

Minecraft is a trademark of Microsoft Corporation.

---

## Installation

Download `NullLauncher.exe` from **GitHub → Releases → Latest Release** and run it.

By default, launcher settings/cache are stored in the normal per-user application-data directory. Minecraft itself still uses the configured Minecraft directory.

### Portable mode

Create an empty file named:

```text
portable.flag
```

next to `NullLauncher.exe` before starting the launcher. NullLauncher will then keep its own state, cache, downloaded launcher data, and separate per-version instances under:

```text
data\
```

next to the executable.

The configured main `.minecraft` directory is not silently moved; only NullLauncher-owned data and separate instances become portable.

---

## Features

- Terminal-first UI with mouse and keyboard navigation
- Vanilla, Forge, NeoForge, Fabric, and Quilt support
- Offline account profiles
- Proxy profiles
- Per-version RAM, resolution, Java, and game-directory settings
- Quick Play with the last launched version and up to 3 favorite versions
- Last-launch timestamp on the home screen
- Optional portable mode via `portable.flag`
- Minecraft news with locale-aware deduplication
- News translation cached only for the currently selected UI language
- Hardened news-image loading (HTTPS-only, private-network blocking, byte and pixel limits)
- SIXEL image previews in supported terminals
- Multilingual interface
- GitHub Releases auto-update with mandatory SHA-256 verification
- Optional Authenticode publisher pinning for signed updates
- Update rollback and post-update health check
- Crash Assistant for common RAM, Java, mod/mixin, native-library, and graphics failures
- Built-in diagnostics
- Configurable launcher theme colors
- Automatic Mojang Java/runtime handling
- Optional file verification before launch

---

## Supported languages

- English
- Русский
- Українська
- Беларуская
- Polski
- Deutsch
- Français
- Español
- Português (Brasil)
- 简体中文
- 日本語
- 한국어

News translations are warmed only for the language selected at startup. Existing cached translations for other languages are retained, but NullLauncher no longer sends translation requests for every supported language on each catalog refresh.

---

## Update security

NullLauncher treats update verification as fail-closed:

1. A release must provide a SHA-256 digest for `NullLauncher.exe`, either through GitHub's asset digest or a published checksum asset such as `NullLauncher.exe.sha256`.
2. The downloaded file must match that SHA-256 exactly.
3. The payload must pass executable/version validation before installation.
4. If Authenticode pinning is configured, the Windows signature must be valid and must match the configured certificate identity.
5. The existing staged install, rollback, and health-check flow is used after validation.

An update without a usable SHA-256 digest is rejected instead of being trusted merely because it begins with the `MZ` executable header.

### Authenticode pinning

Set one or both values in `null_launcher/config.py` before publishing builds signed by your certificate:

```python
UPDATE_SIGNER_SUBJECT = "CN=Your Publisher Name, ..."
UPDATE_SIGNER_THUMBPRINT = "YOUR_CERTIFICATE_THUMBPRINT"
```

When either value is configured, update installation requires a valid Authenticode signature matching the configured value. Leave both empty only while the project does not yet have a real signing certificate; do not invent a publisher identity.

The release workflow can sign the final EXE when these GitHub Actions secrets are configured:

```text
WINDOWS_CERTIFICATE_BASE64
WINDOWS_CERTIFICATE_PASSWORD
```

`WINDOWS_CERTIFICATE_BASE64` should contain the Base64 representation of the PFX certificate. After signing, the workflow creates `NullLauncher.exe.sha256` from the final signed executable and uploads both files to the release.

---

## Release pipeline

The Windows workflow separates build and release privileges:

- the build job has `contents: read` only;
- the release job receives `contents: write` only for tag builds;
- third-party/built-in GitHub Actions are pinned to full commit SHAs;
- Python is pinned for the release build;
- PyInstaller and Pillow are pinned to exact versions;
- release-critical Windows wheels are reinstalled using hashes from `requirements-build.lock`;
- tests run before packaging;
- the tag must match `APP_VERSION`;
- the built/signed EXE is smoke-tested before release;
- a SHA-256 sidecar is generated from the exact final artifact.

For a local release-style build:

```powershell
.\scripts\build.ps1
```

---

## Project structure

```text
NullLauncher/
├── .github/
│   └── workflows/
│       └── build-windows.yml
├── null_launcher/
│   ├── __init__.py
│   ├── __main__.py
│   ├── app.py
│   ├── catalog.py
│   ├── cli.py
│   ├── config.py
│   ├── crash_assistant.py
│   ├── diagnostics.py
│   ├── download_cache.py
│   ├── localization.py
│   ├── minecraft.py
│   ├── news.py
│   ├── news_ui.py
│   ├── profiles_ui.py
│   ├── state.py
│   ├── terminal.py
│   ├── updater.py
│   ├── utils.py
│   └── window_terminal.py
├── scripts/
│   ├── build.ps1
│   ├── sync_version_info.py
│   └── verify_windows_build.py
├── tests/
│   ├── test_features.py
│   ├── test_news.py
│   ├── test_smoke.py
│   └── test_updater.py
├── NullLauncher.py
├── NullLauncher.spec
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── requirements-build.lock
├── .gitignore
└── README.md
```

`config.py` now contains runtime/configuration constants, while the large localization tables live in `localization.py`. News and account/proxy screen logic are separated from the main application class through small UI mixins.

---

## Requirements

### For users

Nothing besides a supported Windows environment. The release EXE bundles the Python runtime and required libraries.

### For development

- Windows 10 / 11
- Python 3.10+
- PowerShell
- Git

Runtime dependencies are in `requirements.txt`. Development/build dependencies are in `requirements-dev.txt`; release-critical wheel hashes are in `requirements-build.lock`.

---

## Building from source

```powershell
git clone https://github.com/BrawliPup12/NullLauncher.git
cd NullLauncher
.\scripts\build.ps1
```

If PowerShell blocks script execution:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\build.ps1
```

The compiled executable will appear at:

```text
dist\NullLauncher.exe
```

---

## Running from source

```powershell
python -m pip install -r requirements.txt
python NullLauncher.py
```

Available commands:

```powershell
python NullLauncher.py
python NullLauncher.py --version
python NullLauncher.py --diagnose
```

When a required runtime dependency is missing in source mode, NullLauncher installs the exact configured version rather than an arbitrary newer Pillow release.

---

## Tests

```powershell
python -m pytest -q
```

The suite includes regression coverage for localized-news deduplication, current-language-only translation, update checksum enforcement, news-image hardening, Crash Assistant signatures, Quick Play state, and portable mode.

---

## Contributing

Issues and pull requests are welcome. For a reproducible bug report, include:

- NullLauncher version
- Windows version
- terminal used
- Minecraft version
- error message or relevant log
- steps to reproduce

---

## Author

Created by [BrawliPup12](https://github.com/BrawliPup12)

---

## Download

Latest releases:

https://github.com/BrawliPup12/NullLauncher/releases
