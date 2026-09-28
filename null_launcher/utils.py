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

from . import config as cfg
from .config import ANSI_RE, APP_NAME, BIG_LOGO, BOLD, RESET, SMALL_LOGO

                                                                             

def _cell_width_char(ch: str) -> int:
    if not ch or unicodedata.combining(ch) or ch in {"\ufe0e", "\ufe0f", "\u200d"}:
        return 0
    return 2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 1


def plain_cell_width(text: str) -> int:
    return sum(_cell_width_char(ch) for ch in str(text))


def visible_len(text: str) -> int:
    return plain_cell_width(ANSI_RE.sub("", str(text)))


def _take_cells(text: str, width: int) -> tuple[str, str]:
    """Split plain text at an exact terminal-cell budget without cutting combining marks."""
    if width <= 0:
        return "", text
    used = 0
    pos = 0
    for i, ch in enumerate(text):
        cw = _cell_width_char(ch)
        if used + cw > width:
            break
        used += cw
        pos = i + 1
    return text[:pos], text[pos:]


def _tail_cells(text: str, width: int) -> str:
    if plain_cell_width(text) <= width:
        return text
    out: list[str] = []
    used = 0
    for ch in reversed(text):
        cw = _cell_width_char(ch)
        if used + cw > width:
            break
        out.append(ch)
        used += cw
    return "".join(reversed(out))


def center_ansi(text: str, width: int) -> str:
    """Center ANSI-decorated text using actual terminal cell width (including CJK)."""
    return " " * max(0, (int(width) - visible_len(text)) // 2) + text


def clean_markup(value: Any) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return re.sub(r"\s+", " ", text).strip()


def _scale_logo_rows(rows: list[str], target_width: int, target_height: int) -> list[str]:
    """Shrink the original logo without stretching its cell aspect ratio."""
    if not rows:
        return []
    sw, sh = max(len(r) for r in rows), len(rows)
    width_limit = max(1, min(sw, int(target_width)))
    height_limit = max(1, min(sh, int(target_height)))
    scale = min(1.0, width_limit / max(1, sw), height_limit / max(1, sh))
    tw = max(1, min(sw, int(round(sw * scale))))
    th = max(1, min(sh, int(round(sh * scale))))
    padded = [r.ljust(sw) for r in rows]
    out: list[str] = []
    for ty in range(th):
        sy = min(sh - 1, int((ty + 0.5) * sh / th))
        chars: list[str] = []
        for tx in range(tw):
            x0 = int(tx * sw / tw)
            x1 = max(x0 + 1, int((tx + 1) * sw / tw))
            region = padded[sy][x0:x1]
                                                                                
                                                                                     
            non_space = [c for c in region if c != " "]
            if not non_space:
                chars.append(" ")
            else:
                priority = ("|", "\\", "/", "_", "`", "'", ".")
                chosen = next((p for p in priority if p in non_space), non_space[len(non_space) // 2])
                chars.append(chosen)
        out.append("".join(chars))

                                                                              
                                                                            
                                                                                
                                                     
    nonempty = [row for row in out if row.strip()]
    if not nonempty:
        return [SMALL_LOGO]
    left = min((len(row) - len(row.lstrip(" ")) for row in nonempty), default=0)
    right = max((len(row.rstrip(" ")) for row in nonempty), default=1)
    right = max(left + 1, right)
    rect = [row[left:right] for row in nonempty]
    rw = max(len(row) for row in rect)
    return [row.ljust(rw) for row in rect]

def brand_logo_lines(width: int, height: int = 0) -> list[str]:
    width, height = max(12, int(width)), max(0, int(height))
    usable = max(8, width - 4)
    source_w = max(map(len, BIG_LOGO))
    source_h = len(BIG_LOGO)
    if usable >= source_w and (not height or height >= source_h + 5):
        raw = BIG_LOGO
    elif usable >= 24:
        height_room = source_h if not height else max(2, min(source_h, height - 6))
        raw = _scale_logo_rows(BIG_LOGO, usable, height_room)
    else:
        raw = [SMALL_LOGO[:max(1, usable)]]
    return [f"{cfg.PRIMARY_COLOR}{BOLD}{line}{RESET}" for line in raw]


def wrap_plain(text: Any, width: int, *, max_lines: int = 0) -> list[str]:
    """Responsive wrapping by terminal cells, so Latin and CJK resize consistently."""
    width = max(1, int(width))
    source = clean_markup(text)
    if not source:
        return [""]
    words = source.split(" ")
    lines: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current:
            lines.append(current)
            current = ""

    for word in words:
        if not word:
            continue
        candidate = word if not current else current + " " + word
        if plain_cell_width(candidate) <= width:
            current = candidate
            continue
        flush()
        remaining = word
        while plain_cell_width(remaining) > width:
            chunk, remaining = _take_cells(remaining, width)
            if not chunk:                                                       
                chunk, remaining = remaining[:1], remaining[1:]
            lines.append(chunk)
        current = remaining
    flush()
    if not lines:
        lines = [""]
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        room = max(1, width - 1)
        head, _ = _take_cells(lines[-1], room)
        lines[-1] = head.rstrip() + "…"
    return lines


def _cmd_filter_image(image: Any, max_pixel_width: int, max_pixel_height: int) -> Any:
    from PIL import Image, ImageDraw, ImageEnhance, ImageOps
    source = ImageOps.exif_transpose(image).convert("RGB")
    if source.width <= 0 or source.height <= 0:
        raise ValueError("Empty article image")
    target_w, target_h = max(32, int(max_pixel_width)), max(24, int(max_pixel_height))
                                                                                 
                                                                               
    resized = ImageOps.contain(source, (target_w, target_h), method=Image.Resampling.LANCZOS)
    gray = ImageOps.autocontrast(resized.convert("L"), cutoff=1)
    gray = ImageEnhance.Contrast(gray).enhance(1.24)
    gray = gray.point(lambda v: int(round(v / 17.0)) * 17)
    r, g, b = cfg.NEWS_FILTER_RGB
    dark = (max(0, r // 40), max(0, g // 40), max(0, b // 40))
    light = (max(48, r), max(48, g), max(48, b))
    styled = ImageOps.colorize(gray, black=dark, white=light)
    px = styled.load()
    for y in range(2, styled.height, 4):
        for x in range(styled.width):
            pr, pg, pb = px[x, y]
            px[x, y] = (int(pr * 0.64), int(pg * 0.68), int(pb * 0.68))
                                                                                  
                                                                              
    if styled.width >= 4 and styled.height >= 4:
        border = tuple(max(28, min(255, int(c * 0.72))) for c in light)
        ImageDraw.Draw(styled).rectangle((0, 0, styled.width - 1, styled.height - 1), outline=border, width=1)
    return styled


def _sixel_rle(values: list[int]) -> str:
    if not values:
        return ""
    out: list[str] = []
    i = 0
    n = len(values)
    while i < n:
        value = values[i]
        j = i + 1
        while j < n and values[j] == value:
            j += 1
        count = j - i
        ch = chr(63 + value)
        if count >= 4:
            out.append(f"!{count}{ch}")
        else:
            out.append(ch * count)
        i = j
    return "".join(out)


def encode_sixel(image: Any, colors: int = 16) -> str:
    """Encode a Pillow image as an inline SIXEL DCS sequence without extra deps."""
    from PIL import Image

    rgb = image.convert("RGB")
    colors = max(4, min(32, int(colors)))
    quantized = rgb.quantize(
        colors=colors,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.FLOYDSTEINBERG,
    )
    width, height = quantized.size
    data = list(quantized.getdata())
    used = sorted(set(data))
    compact = {old: new for new, old in enumerate(used)}
    palette = quantized.getpalette() or []

    parts: list[str] = ["\x1bP0;1q", f'"1;1;{width};{height}']
    for old in used:
        base = old * 3
        r = palette[base] if base < len(palette) else 0
        g = palette[base + 1] if base + 1 < len(palette) else 0
        b = palette[base + 2] if base + 2 < len(palette) else 0
        parts.append(
            f"#{compact[old]};2;{round(r * 100 / 255)};{round(g * 100 / 255)};{round(b * 100 / 255)}"
        )

    for y0 in range(0, height, 6):
        any_color = False
        for old in used:
            values: list[int] = []
            last_nonzero = -1
            for x in range(width):
                bits = 0
                for bit in range(6):
                    y = y0 + bit
                    if y < height and data[y * width + x] == old:
                        bits |= 1 << bit
                values.append(bits)
                if bits:
                    last_nonzero = x
            if last_nonzero < 0:
                continue
            any_color = True
            parts.append(f"#{compact[old]}")
            parts.append(_sixel_rle(values[: last_nonzero + 1]))
            parts.append("$")
        if any_color and parts[-1] == "$":
            parts.pop()
        if y0 + 6 < height:
            parts.append("-")
    parts.append("\x1b\\")
    return "".join(parts)


def _sixel_geometry(
    image: Any,
    cell_columns: int,
    cell_rows: int,
    cell_px: tuple[int, int],
    display_scale: float = 1.0,
) -> tuple[int, int, int, int]:
    """Return encoded_px_w/h and the real occupied terminal cells.

    Windows Terminal reports console font metrics in logical pixels while SIXEL
    is rasterized in display pixels.  On a 125/150/175% desktop this mismatch
    made the launcher think an image was wider/taller than it really was, so the
    cursor origin drifted left and the text was pushed too far down.
    """
    cell_w = max(4, int(cell_px[0]))
    cell_h = max(8, int(cell_px[1]))
    scale = max(0.45, min(1.5, float(display_scale or 1.0)))
    max_display_w = max(32.0, int(cell_columns) * cell_w)
    max_display_h = max(24.0, int(cell_rows) * cell_h)
    max_encoded_w = max(32, int(round(max_display_w / scale)))
    max_encoded_h = max(24, int(round(max_display_h / scale)))
    sw = max(1, int(getattr(image, "width", 1)))
    sh = max(1, int(getattr(image, "height", 1)))
    ratio = min(max_encoded_w / sw, max_encoded_h / sh)
    ew = max(1, int(round(sw * ratio)))
    eh = max(1, int(round(sh * ratio)))
    occupied_cols = max(1, int((ew * scale + cell_w - 1) // cell_w))
    occupied_rows = max(1, int((eh * scale + cell_h - 1) // cell_h))
    return ew, eh, occupied_cols, occupied_rows


def sixel_preview(
    image: Any,
    cell_columns: int,
    cell_rows: int,
    cell_px: tuple[int, int],
    display_scale: float = 1.0,
) -> tuple[str, int, int]:
    """Return (payload, occupied_cell_columns, occupied_cell_rows)."""
    ew, eh, occupied_cols, occupied_rows = _sixel_geometry(
        image, cell_columns, cell_rows, cell_px, display_scale
    )
    styled = _cmd_filter_image(image, ew, eh)
    payload = encode_sixel(styled, colors=8)
    return payload, occupied_cols, occupied_rows


def decode_article_image(raw: bytes) -> Any:
    """Decode image bytes once; resize/filter happens only when the terminal changes."""
    from PIL import Image

    with Image.open(io.BytesIO(raw)) as opened:
        opened.load()
        return opened.convert("RGB").copy()

def clip(text: str, width: int) -> str:
    """Clip text to terminal cells; preserves ANSI when no clipping is needed."""
    if width <= 0:
        return ""
    clean = ANSI_RE.sub("", str(text))
    if plain_cell_width(clean) <= width:
        return text
    if width == 1:
        return "…"
    head, _ = _take_cells(clean, width - 1)
    return head.rstrip() + "…"


def safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(minimum, min(maximum, n))


def app_data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / APP_NAME


def default_minecraft_dir() -> str:
    if os.name == "nt":
        return str(Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / ".minecraft")
    if sys.platform == "darwin":
        return str(Path.home() / "Library" / "Application Support" / "minecraft")
    return str(Path.home() / ".minecraft")


def total_memory_mb() -> int:
    try:
        if os.name == "nt":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return int(stat.ullTotalPhys // 1024 // 1024)
        if hasattr(os, "sysconf"):
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            return int(pages * page_size // 1024 // 1024)
    except Exception:
        pass
    return 8192


def recommended_ram_mb() -> int:
    total = total_memory_mb()
    return max(2048, min(6144, total // 3))


def offline_uuid(username: str) -> str:
    """Java UUID.nameUUIDFromBytes(b'OfflinePlayer:' + username), as hex."""
    digest = bytearray(hashlib.md5(("OfflinePlayer:" + username).encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(digest)).hex


PROXY_HOST_RE = re.compile(r"^[A-Za-z0-9._:\-\[\]]+$")


def valid_proxy_host(value: str) -> bool:
    host = str(value or "").strip()
    return bool(host and len(host) <= 255 and PROXY_HOST_RE.fullmatch(host))


def proxy_protocol(profile: Optional[dict[str, Any]]) -> str:
    """Return canonical SOCKS protocol, migrating legacy profiles."""
    if not profile:
        return ""
    raw = str(profile.get("protocol", "")).strip().lower().replace("_", "-")
    aliases = {
        "socks4": "socks4",
        "socks-4": "socks4",
        "socks5": "socks5",
        "socks-5": "socks5",
    }
    if raw in aliases:
        return aliases[raw]
    if raw:
                                                                                 
        return ""
                                                          
    try:
        return "socks4" if int(profile.get("version", 5)) == 4 else "socks5"
    except (TypeError, ValueError):
        return "socks5"


def proxy_label(profile: Optional[dict[str, Any]]) -> str:
    proto = proxy_protocol(profile)
    return {"socks4": "SOCKS4", "socks5": "SOCKS5"}.get(proto, "SOCKS")


def proxy_jvm_arguments(profile: Optional[dict[str, Any]]) -> list[str]:
    """Translate a SOCKS profile into Java networking properties."""
    if not profile:
        return []
    host = str(profile.get("host", "")).strip()
    protocol = proxy_protocol(profile)
    try:
        port = int(profile.get("port", 1080))
    except (TypeError, ValueError):
        return []
    if protocol not in ("socks4", "socks5") or not valid_proxy_host(host) or not 1 <= port <= 65535:
        return []
    version = 4 if protocol == "socks4" else 5
    return [
        f"-DsocksProxyHost={host}",
        f"-DsocksProxyPort={port}",
        f"-DsocksProxyVersion={version}",
    ]


def proxy_game_arguments(profile: Optional[dict[str, Any]]) -> list[str]:
    """Minecraft's own proxy arguments represent a SOCKS connection."""
    if not profile or proxy_protocol(profile) not in ("socks4", "socks5"):
        return []
    host = str(profile.get("host", "")).strip()
    try:
        port = int(profile.get("port", 1080))
    except (TypeError, ValueError):
        return []
    if not valid_proxy_host(host) or not 1 <= port <= 65535:
        return []
    return ["--proxyHost", host, "--proxyPort", str(port)]

def centered_splash_progress(width: int, value: int, maximum: int) -> str:
    """Build a centered progress/status row with no activity text beside it."""
    width = max(12, int(width))
    counter = f"{value}/{maximum}"
    ratio = 0.0 if maximum <= 0 else max(0.0, min(1.0, value / maximum))
    bar_width = max(4, min(42, width - len(counter) - 4))
    done = int(bar_width * ratio)
    bar = "█" * done + "░" * (bar_width - done)
    return center_ansi(f"{cfg.PRIMARY_COLOR}{bar}{RESET}  {counter}", width)


def slug(text: str) -> str:
    out = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._")
    return out[:80] or "instance"


def atomic_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


def load_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def tail_text(path: Path, max_lines: int = 20) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-max_lines:]).strip()
    except OSError:
        return ""


def normalize_version_type(value: str) -> str:
    mapping = {
        "release": "release",
        "snapshot": "snapshot",
        "old_beta": "beta",
        "old_alpha": "alpha",
    }
    return mapping.get(value, value or "unknown")


def natural_version_key(value: str) -> tuple[tuple[int, Any], ...]:
    """Comparable fallback key for Minecraft-ish version identifiers."""
    parts = re.findall(r"\d+|\D+", str(value).lower())
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in parts)
