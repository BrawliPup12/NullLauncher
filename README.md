# NullLauncher

![GitHub Release](https://img.shields.io/github/v/release/BrawliPup12/NullLauncher)
![GitHub Downloads](https://img.shields.io/github/downloads/BrawliPup12/NullLauncher/total)
![Platform](https://img.shields.io/badge/platform-Windows-blue)

A lightweight, modular terminal-styled Minecraft launcher for Windows.

NullLauncher is built around a simple idea:

> No unnecessary bloat. No heavy framework. Fully modular. Just launch Minecraft.

It renders in its own native lightweight terminal-styled window (or directly inside Windows Terminal / CMD) and features a **fully modular architecture** where every screen and subsystem is a module that can be toggled, extended, or completely replaced without breaking the launcher.

---

## Disclaimer

NullLauncher is an independent project and is not affiliated with, endorsed by, or sponsored by Mojang Studios or Microsoft.

Minecraft is a trademark of Microsoft Corporation.

---

## Installation

Download `NullLauncher.exe` from **GitHub → Releases → Latest Release** and run it.

By default, launcher settings, cache, and external modules are stored in `%APPDATA%\NullLauncher`. Minecraft itself uses the configured `.minecraft` directory.

### Portable mode

Create an empty file named:

```text
portable.flag
```

next to `NullLauncher.exe` before starting the launcher. NullLauncher will then keep its own state, cache, modules (`data\modules\`), and separate per-version instances under:

```text
data\
```

next to the executable.

---

## Features

- **Fully Modular Architecture**: Every core subsystem (`play`, `versions`, `accounts`, `modules`, `settings`) is a module; drop `.py` files into `modules/` to add new features or replace built-in screens
- **Fast, Lean Core Startup**: Optional subsystems are not hardcoded into the startup path — if the `news` module is not installed, startup skips news fetching and translation completely
- **Official vs. Unofficial Module Verification**: Cryptographic SHA-256 verification (evaluated before module execution) distinguishes genuine **Official NullLauncher Modules (`★`)** from third-party/community modules
- **Fault-Isolated Plugin Execution**: Syntax errors or runtime exceptions inside any external module are caught and isolated so the launcher never crashes
- **Native Windowed Terminal UI & Console Support**: Runs as a standalone native GUI window with keyboard and mouse navigation, or in classic console mode (`--console`)
- **Vanilla, Forge, NeoForge, Fabric, and Quilt** support
- **Offline Account Profiles** with deterministic offline UUIDs
- **Per-version Settings**: Custom RAM (`-Xms` / `-Xmx`), resolution, Java path, and isolated instance directories
- **Quick Play**: Launch the last played version or up to 3 favorite versions directly from the home screen
- **Multilingual Interface**: 12 built-in languages with an extensible translation registry for external modules
- **GitHub Releases Auto-Update** with mandatory SHA-256 verification, staged replacement, rollback, and post-update health checks
- **Crash Assistant** for common RAM, Java, mod/mixin, native-library, and graphics failures
- **Automatic Mojang Java Runtime** detection and installation

---

## External Modules

To keep the core launcher fast and lightweight, optional features are distributed as standalone `.py` modules that you can drop into your `modules/` folder (**Modules → Open modules folder** inside the launcher, or `%APPDATA%\NullLauncher\modules\`):

### Official NullLauncher Modules (`official_modules/`)

Verified automatically by NullLauncher via pinned pre-execution SHA-256 digests (`★ Official NullLauncher module`):

| Module | File | ID | Description |
| :--- | :--- | :--- | :--- |
| **Minecraft News** | [`official_modules/news.py`](official_modules/news.py) | `news` | Official Minecraft news feed with localized article translations, SIXEL cover previews, and Settings integration. |
| **Proxies** | [`official_modules/proxies.py`](official_modules/proxies.py) | `proxies` | SOCKS4 / SOCKS5 proxy profile manager and active proxy status line in the main menu header. |

### Community / Third-Party Modules (`community_modules/`)

| Module | File | ID | Description |
| :--- | :--- | :--- | :--- |
| **Modrinth Mods** | [`community_modules/modrinth.py`](community_modules/modrinth.py) | `modrinth` | Search, download, and manage Minecraft mods directly from Modrinth into `mods/` with automatic MC/loader filtering, SHA-512 verification, and required dependency installation. |

> **Note:** `modrinth.py` is currently provided as a community/third-party module and is not part of the pinned official module set.

---

## Module Development Guide (For Developers)

NullLauncher is designed so anyone can write a new module or replace an existing built-in screen by placing a single `.py` file (or a package directory with `__init__.py`) into the `modules/` folder:

- **Standard mode:** `%APPDATA%\NullLauncher\modules\`
- **Portable mode:** `data\modules\`
- **Custom path override:** set the `NULLLAUNCHER_MODULES_DIR` environment variable

After adding or editing a file, either restart NullLauncher or click **Modules → Reload modules** in the UI.

### 1. Full Class-Based Module (`LauncherModule`)

Subclass `LauncherModule` from `null_launcher.modules` for full control over menus, headers, settings, version catalog providers, and game launch hooks:

```python
from __future__ import annotations

from typing import Any
from null_launcher.modules import (
    LauncherModule,
    MenuContribution,
    ModuleContext,
    ModuleMetadata,
    SettingContribution,
)
from null_launcher.terminal import MenuItem


class MyCustomModule(LauncherModule):
    meta = ModuleMetadata(
        id="my_custom_tool",
        name_key="my_tool_title",
        description_key="my_tool_desc",
        version="1.0.0",
        author="YourName",
        order=35,          # Controls position in the main menu (Play=10, Versions=20, Accounts=30, Modules=50, Settings=60)
        can_disable=True,
    )

    # Optional: register translations for any language (automatically falls back to "en")
    translations = {
        "en": {
            "my_tool_title": "My Custom Tool",
            "my_tool_desc": "Adds a custom screen and JVM launch flag.",
            "my_tool_hello": "Hello from MyCustomModule!",
        },
        "ru": {
            "my_tool_title": "Мой модуль",
            "my_tool_desc": "Добавляет свой экран и аргумент запуска JVM.",
            "my_tool_hello": "Привет из MyCustomModule!",
        },
    }

    def get_menu_items(self, ctx: ModuleContext) -> list[MenuContribution]:
        return [
            MenuContribution(
                id="my_custom_tool",
                label=ctx.tr("my_tool_title"),
                order=self.meta.order,
                location="main",  # "main" or "bottom"
            )
        ]

    def open_screen(self, ctx: ModuleContext) -> None:
        state = ctx.module_state(self.meta.id)
        visits = int(state.get("visits", 0)) + 1
        state["visits"] = visits
        ctx.save_state()

        ctx.menu.choose(
            ctx.tr("my_tool_title"),
            [MenuItem(ctx.tr("back"), "back")],
            subtitle=f"{ctx.tr('my_tool_hello')} (visits: {visits})",
            spacer_after_title=True,
        )

    def on_before_launch(self, ctx: ModuleContext, version_id: str, options: dict[str, Any]) -> None:
        options.setdefault("jvmArguments", []).append("-Dmy.custom.module=true")
```

### 2. Minimal Single-File Script Style

For quick scripts, you don't even need to define a class — just define `MODULE_ID` and hook functions at the top level of your `.py` file:

```python
MODULE_ID = "fps_boost_flags"
MODULE_NAME = "JVM GC Flags"
MODULE_DESCRIPTION = "Appends custom G1GC flags before launching Minecraft."
MODULE_VERSION = "1.0.0"
MODULE_AUTHOR = "You"
MODULE_ORDER = 45


def open_screen(ctx):
    ctx.message("JVM GC Flags", "Custom G1GC flags are active!")


def on_before_launch(ctx, version_id, options):
    options.setdefault("jvmArguments", []).extend([
        "-XX:+UnlockExperimentalVMOptions",
        "-XX:+UseG1GC",
    ])
```

### 3. Replacing Built-in Core Modules

Any built-in module (`play`, `versions`, `accounts`, `modules`, `settings`) or external slot (`proxies`, `news`) can be replaced by setting `replaces="<slot_id>"` in `ModuleMetadata`:

```python
class CustomVersionsModule(LauncherModule):
    meta = ModuleMetadata(
        id="custom_versions",
        name="Custom Versions Browser",
        replaces="versions",  # Overrides the built-in Versions module
        order=20,
    )

    def get_menu_items(self, ctx: ModuleContext) -> list[MenuContribution]:
        return [MenuContribution(id="versions", label="Custom Versions", order=20)]

    def open_screen(self, ctx: ModuleContext) -> None:
        ...
```

If the user disables or deletes `custom_versions.py` in the **Modules** manager, NullLauncher automatically restores the original built-in `versions` module without restarting.

### 4. Adding Custom Version Catalog Providers (`VersionSourceProvider`)

Modules can inject new version tabs, entries, and installers directly into the **Versions** screen by implementing `VersionSourceProvider` and returning it from `get_version_providers(self, ctx: ModuleContext)`:

- `filter_tabs(ctx) -> list[tuple[str, str]]`: adds filter tabs to the Versions screen
- `build_entries(ctx, filter_kind, query) -> list[VersionEntry]`: supplies catalog rows
- `can_handle(ctx, entry) -> bool`: claims installation handling for matching entries
- `install(ctx, entry) -> str`: performs installation and returns the installed version ID

### 5. Available `LauncherModule` Hooks & `ModuleContext` API

| Hook / Method | Purpose |
| :--- | :--- |
| `on_load(ctx)` / `on_unload(ctx)` | Called when the module becomes active or is deactivated/reloaded. |
| `get_header_lines(ctx) -> list[str]` | Contributes status lines to the top header on the home screen. |
| `get_menu_items(ctx) -> list[MenuContribution]` | Adds items to the main menu (`location="main"`) or bottom bar (`location="bottom"`). |
| `get_settings_items(ctx) -> list[SettingContribution]` | Adds custom rows to the **Settings** screen under *Module settings*. |
| `get_version_providers(ctx) -> list[VersionSourceProvider]` | Extends the **Versions** catalog with custom loaders or modpacks. |
| `get_preload_tasks(ctx) -> list[tuple[str, Callable]]` | Runs background preload tasks during the startup splash screen. |
| `on_before_launch(ctx, version_id, options)` | Mutates `options` (`jvmArguments`, `gameDirectory`, etc.) before command generation. |
| `on_modify_command(ctx, version_id, command, options)` | Inspects or modifies the final `java` command list before `Popen`. |
| `on_after_launch(ctx, version_id, exit_code)` | Called after the Minecraft process exits. |

`ctx` (`ModuleContext`) provides safe access to:
- `ctx.store` (`StateStore`), `ctx.module_state(module_id)` (persistent per-module JSON dictionary in `state.json`), and `ctx.save_state()`
- `ctx.menu` (`Menu`), `ctx.term` (`Terminal`), `ctx.data` (`LauncherData`), `ctx.download_cache` (`DownloadCache`)
- `ctx.tr(key, **kwargs)`, `ctx.message(title, text, error=False)`, `ctx.confirm(title, text)`, `ctx.prompt(label, default="")`, `ctx.progress_task(title, fn)`

### 6. Official Module Authenticity Verification

NullLauncher verifies official external modules in `null_launcher/modules/trust.py`:
- Raw file bytes are read from disk **before** `exec_module` executes the module's Python code.
- The normalized SHA-256 digest is compared in constant time (`hmac.compare_digest`) against an immutable closure table of official module digests.
- Setting `author="NullLauncher"` or attempting to monkeypatch `trust` at import time cannot spoof the `★ Official NullLauncher module` badge.

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

---

## Update security

NullLauncher treats update verification as fail-closed:

1. A release must provide a SHA-256 digest for `NullLauncher.exe`, either through GitHub's asset digest or `NullLauncher.exe.sha256`.
2. The downloaded file must match that SHA-256 exactly.
3. The payload must pass executable/version validation before installation.
4. If Authenticode pinning is configured, the Windows signature must be valid and must match the configured certificate identity.
5. Staged install, rollback, and post-update health-check flows protect against broken updates.

---

## Project structure

```text
NullLauncher/
├── .github/
│   └── workflows/
│       └── build-windows.yml
├── community_modules/
│   └── modrinth.py              # Community Modrinth browser & downloader module
├── official_modules/
│   ├── news.py                  # Official Minecraft News external module (SHA-256 verified)
│   └── proxies.py               # Official SOCKS4/SOCKS5 Proxies external module (SHA-256 verified)
├── null_launcher/
│   ├── modules/
│   │   ├── __init__.py          # Built-in core registry factory
│   │   ├── accounts_module.py   # Offline accounts core module
│   │   ├── base.py              # LauncherModule, ModuleContext, contributions & providers
│   │   ├── modules_module.py    # Modules Manager UI screen
│   │   ├── play_module.py       # Game launch & Quick Play core module
│   │   ├── registry.py          # Dynamic module discovery, override resolution & fault isolation
│   │   ├── settings_module.py   # Launcher settings core module
│   │   ├── trust.py             # Unforgeable SHA-256 verification for official modules
│   │   └── versions_module.py   # Version catalog & loader installer core module
│   ├── __init__.py
│   ├── __main__.py
│   ├── app.py
│   ├── catalog.py
│   ├── cli.py
│   ├── config.py
│   ├── crash_assistant.py
│   ├── diagnostics.py
│   ├── download_cache.py
│   ├── instance.py
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
│   ├── test_modules.py
│   ├── test_news.py
│   ├── test_smoke.py
│   └── test_updater.py
├── NullLauncher.py
├── NullLauncher.spec
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── requirements-build.lock
└── README.md
```

---

## Building & Running from Source

### Run from source

```powershell
python -m pip install -r requirements.txt
python NullLauncher.py
```

Available CLI flags:

```powershell
python NullLauncher.py
python NullLauncher.py --console
python NullLauncher.py --version
python NullLauncher.py --diagnose
```

### Build standalone Windows executable (`dist\NullLauncher.exe`)

```powershell
.\scripts\build.ps1
```

### Run tests

```powershell
python -m pytest -q
```

---

## Author

Created by [BrawliPup12](https://github.com/BrawliPup12)

---

## Download

Latest releases:

https://github.com/BrawliPup12/NullLauncher/releases
