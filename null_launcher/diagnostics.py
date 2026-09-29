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

from .config import APP_NAME, APP_VERSION, tr
from .utils import app_data_dir, default_minecraft_dir, total_memory_mb

                                                                            

def setup_logging(base: Path) -> logging.Logger:
    base.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(APP_NAME)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = RotatingFileHandler(base / "null_launcher.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s"))
    logger.addHandler(handler)
    return logger


def diagnose() -> int:
    base = app_data_dir()
    print(f"{APP_NAME} {APP_VERSION}")
    print(f"{tr('diag_python')}: {sys.version.split()[0]} ({sys.executable})")
    print(f"{tr('diag_os')}: {platform.platform()}")
    print(f"{tr('diag_terminal')}: {shutil.get_terminal_size((100, 32)).columns}x{shutil.get_terminal_size((100, 32)).lines}")
    print(f"{tr('diag_config_dir')}: {base}")
    try:
        base.mkdir(parents=True, exist_ok=True)
        test = base / ".write-test"
        test.write_text("ok", encoding="utf-8")
        test.unlink()
        print(tr("diag_config_writable_yes"))
    except Exception as exc:
        print(tr("diag_config_writable_no", error=exc))
    try:
        import minecraft_launcher_lib as lib
        print(f"minecraft-launcher-lib: {lib.utils.get_library_version()}")
        try:
            print(f"{tr('diag_platform_supported')}: {bool(lib.utils.is_platform_supported())}")
        except Exception:
            pass
    except Exception:
        print(tr("diag_mll_missing"))
    try:
        import PIL
        print(f"Pillow: {PIL.__version__}")
    except Exception:
        print(tr("diag_pillow_missing"))
    print(f"{tr('diag_default_mc_dir')}: {default_minecraft_dir()}")
    print(f"{tr('diag_memory')}: {total_memory_mb()} MB")
    return 0
