from __future__ import annotations

import re
from typing import Any, Optional
from .localization import I18N, LANGUAGES, SPLASH_LINES

APP_NAME = "NullLauncher"
APP_VERSION = "1.11.7"
REQUIRED_MLL = "8.0"
REQUIRED_PILLOW = "12.3.0"
MIN_PYTHON = (3, 10)

UPDATE_REPO_OWNER = "BrawliPup12"
UPDATE_REPO_NAME = "NullLauncher"
UPDATE_REPO_URL = f"https://github.com/{UPDATE_REPO_OWNER}/{UPDATE_REPO_NAME}"
UPDATE_API_LATEST = f"https://api.github.com/repos/{UPDATE_REPO_OWNER}/{UPDATE_REPO_NAME}/releases/latest"
UPDATE_MAX_BYTES = 128 * 1024 * 1024
# Optional Authenticode pinning. Set one or both before publishing signed builds.
# When configured, every downloaded/staged update must have a valid Windows
# signature matching the exact certificate subject and/or SHA-1 thumbprint.
UPDATE_SIGNER_SUBJECT = ""
UPDATE_SIGNER_THUMBPRINT = ""

BIG_LOGO = [
    r" _   _       _ _ _                           _               ",
    r"| \ | |_   _| | | |    __ _ _   _ _ __   ___| |__   ___ _ __ ",
    r"|  \| | | | | | | |   / _` | | | | '_ \ / __| '_ \ / _ \ '__|",
    r"| |\  | |_| | | | |__| (_| | |_| | | | | (__| | | |  __/ |   ",
    r"|_| \_|\__,_|_|_|_____\__,_|\__,_|_| |_|\___|_| |_|\___|_|   ",
]

SMALL_LOGO = "NULLLAUNCHER"

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
MC_NAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
GREEN = "\x1b[92m"
CYAN = "\x1b[96m"
YELLOW = "\x1b[93m"
RED = "\x1b[91m"
GRAY = "\x1b[90m"
WHITE = "\x1b[97m"

DEFAULT_THEME = {
    "theme_primary": "#62F4FF",
    "theme_subtitle": "#9AA4AD",
    "theme_status": "#737D86",
    "news_filter_color": "#62F4FF",
}


CURRENT_LANGUAGE = "en"
PRIMARY_COLOR = "\x1b[38;2;98;244;255m"
SUBTITLE_COLOR = "\x1b[38;2;154;164;173m"
STATUS_COLOR = "\x1b[38;2;115;125;134m"
THEME_PRIMARY_RGB = (98, 244, 255)
NEWS_FILTER_RGB = (98, 244, 255)


def tr(key: str, **values: Any) -> str:
    table = I18N.get(CURRENT_LANGUAGE, I18N["en"])
    text = table.get(key, I18N["en"].get(key, key))
    try:
        return text.format(**values)
    except Exception:
        return text

def _parse_rgb(value: Any, default: str) -> tuple[str, tuple[int, int, int]]:
    raw = str(value or "").strip()
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", raw)
    if m:
        h = m.group(1).upper()
        rgb = tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
        return "#" + h, rgb                              
    m = re.fullmatch(r"\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*", raw)
    if m:
        vals = tuple(max(0, min(255, int(x))) for x in m.groups())
        return "#%02X%02X%02X" % vals, vals                              
    if raw != default:
        return _parse_rgb(default, default)
    return "#FFFFFF", (255, 255, 255)

def normalize_user_color(value: str) -> Optional[str]:
    raw = str(value or "").strip()
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", raw)
    if m:
        return "#" + m.group(1).upper()
    m = re.fullmatch(r"\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*", raw)
    if m:
        vals = tuple(int(x) for x in m.groups())
        if all(0 <= x <= 255 for x in vals):
            return "#%02X%02X%02X" % vals
    return None

def apply_runtime_preferences(settings: dict[str, Any]) -> None:
    global CURRENT_LANGUAGE, PRIMARY_COLOR, SUBTITLE_COLOR, STATUS_COLOR, THEME_PRIMARY_RGB, NEWS_FILTER_RGB
    language = str(settings.get("language") or "en")
    CURRENT_LANGUAGE = language if language in LANGUAGES else "en"
    p, prgb = _parse_rgb(settings.get("theme_primary"), DEFAULT_THEME["theme_primary"])
    s, srgb = _parse_rgb(settings.get("theme_subtitle"), DEFAULT_THEME["theme_subtitle"])
    st, strgb = _parse_rgb(settings.get("theme_status"), DEFAULT_THEME["theme_status"])
    nf, nfrgb = _parse_rgb(settings.get("news_filter_color"), DEFAULT_THEME["news_filter_color"])
    settings["theme_primary"], settings["theme_subtitle"], settings["theme_status"], settings["news_filter_color"] = p, s, st, nf
    PRIMARY_COLOR = f"\x1b[38;2;{prgb[0]};{prgb[1]};{prgb[2]}m"
    SUBTITLE_COLOR = f"\x1b[38;2;{srgb[0]};{srgb[1]};{srgb[2]}m"
    STATUS_COLOR = f"\x1b[38;2;{strgb[0]};{strgb[1]};{strgb[2]}m"
    THEME_PRIMARY_RGB = prgb
    NEWS_FILTER_RGB = nfrgb

def section_label(text: str) -> str:
    return f"──── {text} ────"
