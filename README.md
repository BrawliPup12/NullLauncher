# NullLauncher

![GitHub Release](https://img.shields.io/github/v/release/BrawliPup12/NullLauncher)
![GitHub Downloads](https://img.shields.io/github/downloads/BrawliPup12/NullLauncher/total)
![Platform](https://img.shields.io/badge/platform-Windows-blue)

A lightweight terminal-based Minecraft launcher for Windows.

NullLauncher is designed around a simple idea:

> No unnecessary UI. No heavy framework. Just launch Minecraft.

It runs directly inside Windows Terminal / CMD and supports mouse + keyboard navigation, Minecraft version management, offline accounts, news, proxy profiles, per-version settings, updates, and more.

---

## Disclaimer

NullLauncher is an independent project and is not affiliated with, endorsed by, or sponsored by Mojang Studios or Microsoft.

Minecraft is a trademark of Microsoft Corporation.

NullLauncher may use Minecraft-related files, services, and third-party mod loader projects as part of its functionality.

---

## Screenshots

Screenshots can be added here later.

Example:

```md
![NullLauncher](docs/screenshot-main.png)
```

---

## Installation

Download the latest release from:

**GitHub → Releases → Latest Release**

Then run:

```text
NullLauncher.exe
```

The launcher stores its own configuration and cache files separately from the executable.

---

## Features

- Launch Minecraft directly from terminal
- Vanilla version support
- Forge support
- NeoForge support
- Fabric support
- Quilt support
- Offline accounts
- Multiple account profiles
- Proxy profiles
- Per-version settings
- Custom RAM settings
- Custom resolution
- Separate game directories
- Minecraft news feed
- SIXEL image previews in supported terminals
- Multilingual interface
- Automatic update system through GitHub Releases
- Built-in diagnostics
- Configurable launcher theme colors
- Mouse support in menus
- Automatic Mojang Java/runtime handling
- Optional file verification before launch

---

## Supported languages

NullLauncher currently includes:

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

---

## Project structure

```text
NullLauncher/
│
├── .github/
│   └── workflows/
│       └── build-windows.yml
│
├── null_launcher/
│   ├── __init__.py
│   ├── __main__.py
│   ├── app.py
│   ├── catalog.py
│   ├── cli.py
│   ├── config.py
│   ├── diagnostics.py
│   ├── minecraft.py
│   ├── news.py
│   ├── state.py
│   ├── terminal.py
│   ├── updater.py
│   └── utils.py
│
├── scripts/
│   └── build.ps1
│
├── tests/
│   └── test_smoke.py
│
├── NullLauncher.py
├── NullLauncher.spec
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── .gitignore
└── README.md
```

---

## Requirements

### For users

Nothing.

Download the latest `NullLauncher.exe` from the **Releases** section and run it.

Python is not required.

### For development

- Windows 10 / 11
- Python 3.10+
- PowerShell
- Git

Dependencies:

- `minecraft-launcher-lib`
- `Pillow`

Development dependencies are listed in `requirements-dev.txt`.

---

## Building from source

Clone the repository:

```powershell
git clone https://github.com/BrawliPup12/NullLauncher.git
cd NullLauncher
```

Run the build script:

```powershell
.\scripts\build.ps1
```

If PowerShell blocks script execution:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\build.ps1
```

The compiled executable will appear here:

```text
dist\NullLauncher.exe
```

---

## Running from source

Install dependencies:

```powershell
pip install -r requirements.txt
```

Run:

```powershell
python NullLauncher.py
```

Available commands:

```powershell
python NullLauncher.py
python NullLauncher.py --version
python NullLauncher.py --diagnose
```

---

## Contributing

Issues and pull requests are welcome.

If you find a bug, please include:

- NullLauncher version
- Windows version
- Terminal used
- Minecraft version
- Error message or log
- Steps to reproduce

---

## Author

Created by [BrawliPup12](https://github.com/BrawliPup12)

---

## Download

Latest releases:

https://github.com/BrawliPup12/NullLauncher/releases
