from __future__ import annotations

import argparse
import contextlib
import ctypes
import dataclasses
import hashlib
import html
import io
from html.parser import HTMLParser
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import platform
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import uuid
import unicodedata
import webbrowser
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlencode, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterable, Optional

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
            f"Pillow>={REQUIRED_PILLOW}",
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
            f"Pillow>={REQUIRED_PILLOW}",
        ])
        import importlib
        image_module = importlib.import_module("PIL.Image")
        if term:
            term._init_console()
        return image_module
    except Exception as exc:
        raise RuntimeError(tr("install_pillow_fail")) from exc
