from __future__ import annotations

import subprocess
import sys
from typing import Any, Optional

from .config import APP_NAME, REQUIRED_MLL, REQUIRED_PILLOW, tr
from .terminal import Terminal

                                                                             

MLL: Any = None


def ensure_minecraft_library(term: Optional[Terminal] = None) -> Any:
    global MLL
    try:
        import minecraft_launcher_lib as lib
        version = str(lib.utils.get_library_version())
        if version.split(".")[0] == "8":
            MLL = lib
            return lib
    except Exception:
        pass

    if getattr(sys, "frozen", False):
        raise RuntimeError(tr("bundled_mll_missing"))

    if term:
        term.restore()
        term.clear()
    print(f"{APP_NAME}: {tr('install_mll', version=REQUIRED_MLL)}")
    print(tr("install_mll_desc") + "\n")
    try:
        pip_check = subprocess.run([sys.executable, "-m", "pip", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if pip_check.returncode != 0:
            subprocess.check_call([sys.executable, "-m", "ensurepip", "--upgrade"])
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
            f"minecraft-launcher-lib=={REQUIRED_MLL}",
            f"Pillow=={REQUIRED_PILLOW}",
        ])
        import importlib
        lib = importlib.import_module("minecraft_launcher_lib")
        MLL = lib
        if term:
            term._init_console()
        return lib
    except Exception as exc:
        raise RuntimeError(tr("install_mll_fail")) from exc


def ensure_image_library(term: Optional[Terminal] = None) -> Any:
    """Ensure Pillow is available for real Minecraft.net image rendering."""
    try:
        from PIL import Image
        return Image
    except Exception:
        pass

    if getattr(sys, "frozen", False):
        raise RuntimeError(tr("bundled_pillow_missing"))

    if term:
        term.restore()
        term.clear()
    print(f"{APP_NAME}: {tr('install_pillow')}")
    print(tr("install_pillow_desc") + "\n")
    try:
        pip_check = subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if pip_check.returncode != 0:
            subprocess.check_call([sys.executable, "-m", "ensurepip", "--upgrade"])
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
            f"Pillow=={REQUIRED_PILLOW}",
        ])
        import importlib
        image_module = importlib.import_module("PIL.Image")
        if term:
            term._init_console()
        return image_module
    except Exception as exc:
        raise RuntimeError(tr("install_pillow_fail")) from exc
