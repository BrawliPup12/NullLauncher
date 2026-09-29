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
from .config import ANSI_RE, BOLD, DIM, RESET, WHITE, tr
from .utils import _sixel_geometry, _tail_cells, brand_logo_lines, center_ansi, clean_markup, clip, sixel_preview, visible_len, wrap_plain

                                                                             



def centered_sixel_column(total_columns: int, occupied_columns: int) -> int:
    """1-based VT column that centers a raster footprint in the terminal grid."""
    total = max(1, int(total_columns))
    occupied = max(1, min(total, int(occupied_columns)))
    return max(1, (total - occupied) // 2 + 1)

@dataclasses.dataclass
class InputEvent:
    kind: str
    key: str = ""
    x: int = -1
    y: int = -1
    delta: int = 0


class Terminal:
    """Minimal no-dependency TUI input. Mouse is native on Windows consoles."""

    def __init__(self, mouse_enabled: bool = True):
        self.mouse_enabled = mouse_enabled
        self._win = os.name == "nt"
        self._old_in_mode = None
        self._old_out_mode = None
        self._last_left = False
        self._raw_ctx = None
        self._last_frame: list[str] = []
        self._last_size: Optional[tuple[int, int]] = None
        self._view_key: str = ""
        self._sixel_cache: dict[tuple[Any, ...], tuple[str, int, int]] = {}
        self._last_graphic_key: Optional[tuple[Any, ...]] = None
        self._cell_px_cache: Optional[tuple[int, int]] = None
        self._cell_px_grid: Optional[tuple[int, int]] = None
        self._cell_px_exact = False
        self._sixel_support_cache: Optional[bool] = None
        self._window_icon_handles: list[int] = []
        self._init_console()

    def _init_console(self) -> None:
        if self._win:
            try:
                kernel32 = ctypes.windll.kernel32
                kernel32.SetConsoleOutputCP(65001)
                kernel32.SetConsoleCP(65001)
                if hasattr(sys.stdout, "reconfigure"):
                    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
                    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
                self.hin = kernel32.GetStdHandle(-10)
                self.hout = kernel32.GetStdHandle(-11)
                in_mode = ctypes.c_uint32()
                out_mode = ctypes.c_uint32()
                if kernel32.GetConsoleMode(self.hin, ctypes.byref(in_mode)):
                    self._old_in_mode = in_mode.value
                    mode = in_mode.value | 0x0080 | 0x0008                                
                    if self.mouse_enabled:
                        mode |= 0x0010               
                        mode &= ~0x0040                  
                    kernel32.SetConsoleMode(self.hin, mode)
                if kernel32.GetConsoleMode(self.hout, ctypes.byref(out_mode)):
                    self._old_out_mode = out_mode.value
                    kernel32.SetConsoleMode(self.hout, out_mode.value | 0x0004)                 
            except Exception:
                self._win = False
        self._apply_window_icon()
        self._request_initial_geometry()
        print("\x1b[?25l", end="", flush=True)

    def _apply_window_icon(self) -> None:
        if not self._win or self._window_icon_handles:
            return
        try:
            kernel32 = ctypes.windll.kernel32
            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32

            # Give the launcher a stable Windows application identity. This helps
            # classic console/taskbar grouping use the launcher instead of Python.
            with contextlib.suppress(Exception):
                shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
                shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
                shell32.SetCurrentProcessExplicitAppUserModelID("BrawliPup12.NullLauncher")

            # Explorer caches icons by executable path. Refresh the shell after a
            # rebuilt/updated EXE replaces the previous file at the same path.
            with contextlib.suppress(Exception):
                shell32.SHChangeNotify.restype = None
                shell32.SHChangeNotify(0x08000000, 0x0000, None, None)

            kernel32.GetConsoleWindow.restype = ctypes.c_void_p
            hwnd = kernel32.GetConsoleWindow()
            if not hwnd:
                return

            user32.SendMessageW.restype = ctypes.c_ssize_t
            user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]

            large = ctypes.c_void_p()
            small = ctypes.c_void_p()
            extracted = 0
            if getattr(sys, "frozen", False):
                shell32.ExtractIconExW.argtypes = [
                    ctypes.c_wchar_p, ctypes.c_int,
                    ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint,
                ]
                shell32.ExtractIconExW.restype = ctypes.c_uint
                extracted = int(shell32.ExtractIconExW(str(Path(sys.executable).resolve()), 0, ctypes.byref(large), ctypes.byref(small), 1))

            if extracted > 0:
                if large.value:
                    user32.SendMessageW(hwnd, 0x0080, 1, int(large.value))
                    self._window_icon_handles.append(int(large.value))
                if small.value:
                    user32.SendMessageW(hwnd, 0x0080, 0, int(small.value))
                    self._window_icon_handles.append(int(small.value))
                return

            # Source/dev fallback: load the same ICO that is embedded by PyInstaller.
            roots = []
            bundle_root = getattr(sys, "_MEIPASS", "")
            if bundle_root:
                roots.append(Path(bundle_root))
            roots.append(Path(__file__).resolve().parents[1])
            icon_path = next((root / "assets" / "NullLauncher.ico" for root in roots if (root / "assets" / "NullLauncher.ico").is_file()), None)
            if icon_path is None:
                return
            user32.LoadImageW.restype = ctypes.c_void_p
            user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            for kind, size in ((1, 32), (0, 16)):
                handle = user32.LoadImageW(None, str(icon_path), 1, size, size, 0x0010)
                if handle:
                    user32.SendMessageW(hwnd, 0x0080, kind, int(handle))
                    self._window_icon_handles.append(int(handle))
        except Exception:
            pass

    def _request_initial_geometry(self) -> None:
        """Ask Windows Terminal for a sane first-launch size without fighting later resizes."""
        if not os.environ.get("WT_SESSION"):
            return
        if os.environ.get("NULLLAUNCHER_NO_RESIZE", "").strip().lower() in {"1", "true", "yes", "on"}:
            return
        try:
            current = shutil.get_terminal_size((100, 32))
                                                                                  
                                                                              
            if int(current.columns) < 104 or int(current.lines) < 30:
                target_cols = max(112, int(current.columns))
                target_rows = max(34, min(40, int(current.lines)))
                sys.stdout.write(f"\x1b[8;{target_rows};{target_cols}t")
                sys.stdout.flush()
                time.sleep(0.08)
        except Exception:
            pass

    def restore(self) -> None:
        print(RESET + "\x1b[?25h", end="", flush=True)
        if os.name == "nt":
            try:
                k = ctypes.windll.kernel32
                if self._old_in_mode is not None:
                    k.SetConsoleMode(self.hin, self._old_in_mode)
                if self._old_out_mode is not None:
                    k.SetConsoleMode(self.hout, self._old_out_mode)
            except Exception:
                pass

    def clear(self) -> None:
        self._last_frame = []
        self._last_size = None
        self._last_graphic_key = None
        print("\x1b[2J\x1b[H", end="", flush=True)

    def size(self) -> os.terminal_size:
        return shutil.get_terminal_size((100, 32))

    def render(self, lines: list[str], *, animate: bool = False, delay: float = 0.014) -> bool:
        """Render text responsively and return True when a full repaint happened."""
        size = self.size()
        w = max(1, int(size.columns))
        rows = max(1, int(size.lines))
        frame = list(lines[:rows])
        if len(frame) < rows:
            frame.extend([""] * (rows - len(frame)))

        current_size = (w, rows)
        size_changed = current_size != self._last_size
        old = [] if size_changed else getattr(self, "_last_frame", [])
        full_redraw = bool(animate or not old)
        if size_changed:
            self._last_graphic_key = None

        if full_redraw:
            sys.stdout.write("\x1b[2J\x1b[H")
            sys.stdout.flush()
            if animate:
                for row, line in enumerate(frame, 1):
                    if line:
                        sys.stdout.write(f"\x1b[{row};1H{line}\x1b[K")
                        sys.stdout.flush()
                        time.sleep(delay)
            else:
                                                                                     
                                                                                    
                sys.stdout.write("\x1b[?2026h" + "\n".join(frame) + "\x1b[?2026l")
                sys.stdout.flush()
        else:
            max_rows = max(len(old), len(frame))
            chunks: list[str] = []
            for i in range(max_rows):
                before = old[i] if i < len(old) else ""
                after = frame[i] if i < len(frame) else ""
                if before != after:
                    chunks.append(f"\x1b[{i + 1};1H{after}\x1b[K")
            if chunks:
                sys.stdout.write("\x1b[?2026h" + "".join(chunks) + "\x1b[?2026l")
                sys.stdout.flush()
        self._last_frame = frame
        self._last_size = current_size
        return full_redraw

    @staticmethod
    def _da1_reports_sixel(reply: str) -> Optional[bool]:
        """Parse a Primary Device Attributes (DA1) reply.

        DEC-compatible terminals advertise SIXEL with attribute 4.  Some early
        Windows Terminal 1.22 preview builds accidentally omitted the leading ESC
        in the reply, so accept both ``ESC[?...c`` and ``[?...c``.
        """
        match = re.search(r"(?:\x1b)?\[\?([0-9;]+)c", str(reply or ""))
        if not match:
            return None
        try:
            attrs = {int(x) for x in match.group(1).split(";") if x}
        except ValueError:
            return None
        return 4 in attrs

    def _probe_sixel_support(self) -> Optional[bool]:
        """Ask the active Windows terminal whether it supports SIXEL.

        This is needed when an EXE is launched by double-click while Windows
        Terminal is the system's default terminal host. In that launch path the
        client process may not inherit ``WT_SESSION`` even though SIXEL works.
        """
        if os.name != "nt" or not hasattr(self, "hin"):
            return None
        try:
            import msvcrt

            k = ctypes.windll.kernel32
            old_mode = ctypes.c_uint32()
            if not k.GetConsoleMode(self.hin, ctypes.byref(old_mode)):
                return None
                                                                                 
                                                                                  
            k.SetConsoleMode(self.hin, old_mode.value | 0x0200)
            try:
                sys.stdout.write("\x1b[c")
                sys.stdout.flush()
                deadline = time.monotonic() + 0.20
                reply = ""
                while time.monotonic() < deadline and len(reply) < 128:
                    if msvcrt.kbhit():
                        ch = msvcrt.getwch()
                        reply += ch
                        if ch == "c":
                            parsed = self._da1_reports_sixel(reply)
                            if parsed is not None:
                                return parsed
                    else:
                        time.sleep(0.004)
            finally:
                k.SetConsoleMode(self.hin, old_mode.value)
        except Exception:
            return None
        return None

    def supports_sixel(self) -> bool:
        """Detect SIXEL support without relying only on WT_SESSION."""
        override = os.environ.get("NULLLAUNCHER_SIXEL", "").strip().lower()
        if override in {"1", "true", "yes", "on"}:
            return True
        if override in {"0", "false", "no", "off"}:
            return False

        if self._sixel_support_cache is not None:
            return self._sixel_support_cache

                                                                             
        if os.environ.get("WT_SESSION"):
            self._sixel_support_cache = True
            return True
        term_program = os.environ.get("TERM_PROGRAM", "").lower()
        term = os.environ.get("TERM", "").lower()
        if any(x in term_program for x in ("wezterm", "mlterm")) or "sixel" in term:
            self._sixel_support_cache = True
            return True

                                                                                
                                                                                 
                                             
        probed = self._probe_sixel_support()
        self._sixel_support_cache = bool(probed) if probed is not None else False
        return self._sixel_support_cache

    def _query_xtwinops_cell_pixel_size(self) -> Optional[tuple[int, int]]:
        """Ask Windows Terminal for its *real* cell size via XTWINOPS CSI 16 t.

        GetCurrentConsoleFontEx reports conhost/ConPTY logical metrics and can differ
        materially from the cells Windows Terminal actually uses. SIXEL is rasterized
        using Windows Terminal's own cell size, so using the legacy Win32 metric makes
        a correctly centered image appear shifted left. Query 16 returns
        ``CSI 6 ; height ; width t`` and fixes both image sizing and centering.
        """
        if os.name != "nt" or not hasattr(self, "hin") or not self.supports_sixel():
            return None
        try:
            import msvcrt

            k = ctypes.windll.kernel32
            old_mode = ctypes.c_uint32()
            if not k.GetConsoleMode(self.hin, ctypes.byref(old_mode)):
                return None
                                                                                  
                                                                                     
                                                                                        
            vt_mode = old_mode.value | 0x0200
            k.SetConsoleMode(self.hin, vt_mode)
            try:
                sys.stdout.write("\x1b[16t")
                sys.stdout.flush()
                deadline = time.monotonic() + 0.18
                reply = ""
                while time.monotonic() < deadline and len(reply) < 64:
                    if msvcrt.kbhit():
                        ch = msvcrt.getwch()
                        reply += ch
                        if ch == "t":
                            break
                    else:
                        time.sleep(0.004)
            finally:
                k.SetConsoleMode(self.hin, old_mode.value)
            match = re.search(r"\x1b\[6;(\d+);(\d+)t", reply)
            if match:
                cell_h, cell_w = int(match.group(1)), int(match.group(2))
                if 3 <= cell_w <= 64 and 6 <= cell_h <= 128:
                    return cell_w, cell_h
        except Exception:
            return None
        return None

    def cell_pixel_size(self) -> tuple[int, int]:
        """Return the terminal cell size in pixels, preferring exact WT metrics."""
        grid = (int(self.size().columns), int(self.size().lines))
        if self._cell_px_cache is not None and self._cell_px_grid == grid:
            return self._cell_px_cache

        exact = self._query_xtwinops_cell_pixel_size()
        if exact is not None:
            self._cell_px_cache = exact
            self._cell_px_grid = grid
            self._cell_px_exact = True
            return exact

        self._cell_px_exact = False
        if os.name == "nt" and hasattr(self, "hout"):
            try:
                from ctypes import wintypes

                class COORD(ctypes.Structure):
                    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

                class CONSOLE_FONT_INFOEX(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", wintypes.ULONG),
                        ("nFont", wintypes.DWORD),
                        ("dwFontSize", COORD),
                        ("FontFamily", wintypes.UINT),
                        ("FontWeight", wintypes.UINT),
                        ("FaceName", wintypes.WCHAR * 32),
                    ]

                info = CONSOLE_FONT_INFOEX()
                info.cbSize = ctypes.sizeof(info)
                if ctypes.windll.kernel32.GetCurrentConsoleFontEx(self.hout, False, ctypes.byref(info)):
                    cw, ch = int(info.dwFontSize.X), int(info.dwFontSize.Y)
                    if cw > 0 and ch > 0:
                        self._cell_px_cache = (cw, ch)
                        self._cell_px_grid = grid
                        return self._cell_px_cache
            except Exception:
                pass
        self._cell_px_cache = (8, 16)
        self._cell_px_grid = grid
        return self._cell_px_cache

    def sixel_display_scale(self) -> float:
        """Map logical console pixels to SIXEL display pixels.

        Windows Terminal supports XTWINOPS pixel reports, but mixing terminal
        replies with native ReadConsoleInput mouse events is fragile.  The system
        DPI gives the same scale needed for ConPTY's logical font metrics and does
        not consume input.  An environment override is kept for unusual setups.
        """
        override = os.environ.get("NULLLAUNCHER_SIXEL_SCALE", "").strip()
        if override:
            with contextlib.suppress(Exception):
                return max(0.45, min(1.5, float(override)))
        if self._cell_px_exact:
            return 1.0
        if os.name == "nt" and os.environ.get("WT_SESSION"):
            try:
                dpi = int(ctypes.windll.user32.GetDpiForSystem())
                if dpi > 0:
                    return max(0.45, min(1.5, 96.0 / float(dpi)))
            except Exception:
                pass
        return 1.0

    def sixel_geometry(self, image: Any, cell_columns: int, cell_rows: int) -> tuple[int, int]:
        """Measure the raster footprint in terminal cells without encoding it."""
        _, _, cols, rows = _sixel_geometry(
            image,
            max(1, int(cell_columns)),
            max(1, int(cell_rows)),
            self.cell_pixel_size(),
            self.sixel_display_scale(),
        )
        return cols, rows

    def draw_sixel(
        self,
        image: Any,
        *,
        top_row: int,
        max_columns: int,
        max_rows: int,
        graphic_key: str,
        force: bool = False,
    ) -> bool:
        """Draw the real Pillow image inline using SIXEL, centered in its text box."""
        if image is None or not self.supports_sixel() or max_columns < 4 or max_rows < 2:
            return False
        size = self.size()
        cell_px = self.cell_pixel_size()
        display_scale = self.sixel_display_scale()
        cache_key = (
            graphic_key,
            id(image),
            int(max_columns),
            int(max_rows),
            cell_px,
            round(display_scale, 4),
            cfg.THEME_PRIMARY_RGB,
        )
        rendered = self._sixel_cache.get(cache_key)
        if rendered is None:
            rendered = sixel_preview(image, max_columns, max_rows, cell_px, display_scale)
                                                                              
            if len(self._sixel_cache) > 20:
                self._sixel_cache.clear()
            self._sixel_cache[cache_key] = rendered
        payload, occupied_cols, occupied_rows = rendered
        draw_key = (
            graphic_key,
            int(size.columns),
            int(size.lines),
            int(top_row),
            occupied_cols,
            occupied_rows,
            cell_px,
        )
        if not force and draw_key == self._last_graphic_key:
            return True
        col = centered_sixel_column(int(size.columns), occupied_cols)
        row = max(1, int(top_row))
                                                                                      
                                                                           
        sys.stdout.write(
            "\x1b[?2026h"
            "\x1b7"
            f"\x1b[{row};{col}H"
            + payload
            + "\x1b8"
            "\x1b[?2026l"
        )
        sys.stdout.flush()
        self._last_graphic_key = draw_key
        return True

    def prompt(self, label: str, default: str = "") -> str:
        """Responsive single-line editor so prompts also reflow while the window resizes."""
        self._view_key = "prompt:" + clean_markup(label)
        value = str(default or "")
        first = True
        while True:
            size = self.size()
            w = max(20, int(size.columns))
            h = max(8, int(size.lines))
            content_w = max(16, min(120, w - 4))
            logo = brand_logo_lines(w, h)
            label_lines = wrap_plain(label, max(12, content_w - 2), max_lines=max(1, h // 5))
            help_line = tr("prompt_help")
            help_lines = wrap_plain(help_line, max(12, content_w), max_lines=2)

            input_w = max(8, content_w - 4)
            shown = _tail_cells(value, input_w)
            prompt_text = "> " + shown
            content: list[str] = list(logo) + [""]
            content.extend(f"{BOLD}{line}{RESET}" for line in label_lines)
            content.extend(["", prompt_text, ""])
            content.extend(f"{cfg.SUBTITLE_COLOR}{DIM}{line}{RESET}" for line in help_lines)

            frame = [""] * h
            start = max(0, (h - len(content)) // 2)
            input_row = start + len(logo) + 1 + len(label_lines) + 1
            for i, line in enumerate(content):
                row = start + i
                if row < h:
                    frame[row] = center_ansi(line, w)
            self.render(frame, animate=first)
            first = False

                                                                        
            visible_prompt = prompt_text
            prompt_cells = visible_len(visible_prompt)
            start_col = max(1, (w - prompt_cells) // 2 + 1)
            cursor_col = min(w, start_col + prompt_cells)
            sys.stdout.write(f"\x1b[?25h\x1b[{min(h, input_row + 1)};{cursor_col}H")
            sys.stdout.flush()
            event = self.read_event()
            sys.stdout.write("\x1b[?25l")
            if event.kind == "resize":
                continue
            if event.kind != "key":
                continue
            if event.key == "enter":
                return value.strip() or str(default or "")
            if event.key == "escape":
                return str(default or "")
            if event.key == "backspace":
                value = value[:-1]
            elif len(event.key) == 1 and ord(event.key) >= 32:
                value += event.key

    def read_event(self) -> InputEvent:
        if os.name == "nt" and hasattr(self, "hin"):
            try:
                return self._read_windows_event()
            except Exception:
                pass
        return self._read_posix_event()

    def _read_windows_event(self) -> InputEvent:
        from ctypes import wintypes

        class COORD(ctypes.Structure):
            _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

        class CHAR_UNION(ctypes.Union):
            _fields_ = [("UnicodeChar", wintypes.WCHAR), ("AsciiChar", ctypes.c_char)]

        class KEY_EVENT_RECORD(ctypes.Structure):
            _anonymous_ = ("uChar",)
            _fields_ = [
                ("bKeyDown", wintypes.BOOL),
                ("wRepeatCount", wintypes.WORD),
                ("wVirtualKeyCode", wintypes.WORD),
                ("wVirtualScanCode", wintypes.WORD),
                ("uChar", CHAR_UNION),
                ("dwControlKeyState", wintypes.DWORD),
            ]

        class MOUSE_EVENT_RECORD(ctypes.Structure):
            _fields_ = [
                ("dwMousePosition", COORD),
                ("dwButtonState", wintypes.DWORD),
                ("dwControlKeyState", wintypes.DWORD),
                ("dwEventFlags", wintypes.DWORD),
            ]

        class WINDOW_BUFFER_SIZE_RECORD(ctypes.Structure):
            _fields_ = [("dwSize", COORD)]

        class EVENT_UNION(ctypes.Union):
            _fields_ = [
                ("KeyEvent", KEY_EVENT_RECORD),
                ("MouseEvent", MOUSE_EVENT_RECORD),
                ("WindowBufferSizeEvent", WINDOW_BUFFER_SIZE_RECORD),
                ("padding", ctypes.c_byte * 16),
            ]

        class INPUT_RECORD(ctypes.Structure):
            _anonymous_ = ("Event",)
            _fields_ = [("EventType", wintypes.WORD), ("Event", EVENT_UNION)]

        rec = INPUT_RECORD()
        count = wintypes.DWORD()
        k = ctypes.windll.kernel32
        while True:
            if not k.ReadConsoleInputW(self.hin, ctypes.byref(rec), 1, ctypes.byref(count)):
                return InputEvent("key", "")
            if rec.EventType == 1 and rec.KeyEvent.bKeyDown:
                vk = rec.KeyEvent.wVirtualKeyCode
                keys = {
                    0x26: "up", 0x28: "down", 0x25: "left", 0x27: "right",
                    0x0D: "enter", 0x1B: "escape", 0x08: "backspace",
                    0x21: "pageup", 0x22: "pagedown", 0x24: "home", 0x23: "end",
                    0x2E: "delete",
                }
                if vk in keys:
                    return InputEvent("key", keys[vk])
                ch = rec.KeyEvent.UnicodeChar
                if ch and ord(ch) >= 32:
                    return InputEvent("key", ch)
            elif rec.EventType == 2 and self.mouse_enabled:
                m = rec.MouseEvent
                if m.dwEventFlags == 0:
                    left = bool(m.dwButtonState & 0x0001)
                    pressed = left and not self._last_left
                    self._last_left = left
                    if pressed:
                        return InputEvent("mouse", "click", int(m.dwMousePosition.X), int(m.dwMousePosition.Y))
                elif m.dwEventFlags == 0x0001:               
                    return InputEvent("mouse", "move", int(m.dwMousePosition.X), int(m.dwMousePosition.Y))
                elif m.dwEventFlags == 0x0004:                 
                    delta = ctypes.c_short((int(m.dwButtonState) >> 16) & 0xFFFF).value
                    return InputEvent("mouse", "wheel", int(m.dwMousePosition.X), int(m.dwMousePosition.Y), delta)
            elif rec.EventType == 4:
                return InputEvent("resize")

    def _read_posix_event(self) -> InputEvent:
        if os.name == "nt":
                                                      
            import msvcrt
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                ext = msvcrt.getwch()
                return InputEvent("key", {"H": "up", "P": "down", "K": "left", "M": "right", "I": "pageup", "Q": "pagedown", "G": "home", "O": "end"}.get(ext, ""))
            return InputEvent("key", {"\r": "enter", "\x1b": "escape"}.get(ch, ch))

        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                import select
                if select.select([sys.stdin], [], [], 0.03)[0]:
                    seq = sys.stdin.read(1)
                    if seq == "[":
                        c = sys.stdin.read(1)
                        return InputEvent("key", {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end"}.get(c, "escape"))
                return InputEvent("key", "escape")
            if ch in ("\r", "\n"):
                return InputEvent("key", "enter")
            return InputEvent("key", ch)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


@dataclasses.dataclass
class MenuItem:
    label: str
    value: Any = None
    selectable: bool = True
    hint: str = ""


@dataclasses.dataclass
class MenuGraphic:
    image: Any
    key: str
    rows: int
    columns: int


class Menu:
    def __init__(self, term: Terminal):
        self.term = term

    def choose(
        self,
        title: str,
        items: list[MenuItem],
        *,
        subtitle: str = "",
        subtitle_factory: Optional[Callable[[os.terminal_size], str]] = None,
        graphic_factory: Optional[Callable[[os.terminal_size], Optional[MenuGraphic]]] = None,
        header: Optional[list[str]] = None,
        footer: str = "",
        initial: int = 0,
        allow_escape: bool = True,
        bottom_item: Optional[MenuItem] = None,
        view_key: str = "",
        spacer_after_title: bool = False,
        content_width_limit: int = 140,
        graphic_reserve_rows: int = 0,
    ) -> Any:
        if not items and not (bottom_item and bottom_item.selectable):
            return None
        footer = footer or tr("footer")
        selectable = [i for i, item in enumerate(items) if item.selectable]
        nav: list[Any] = list(selectable)
        BOTTOM = "__bottom__"
        if bottom_item and bottom_item.selectable:
            nav.append(BOTTOM)
        if not nav:
            return None

        selected: Any = initial if initial in selectable else nav[0]
        scroll = 0
        key = view_key or clean_markup(title) or "menu"
        animate_first = key != getattr(self.term, "_view_key", "")
        self.term._view_key = key

        def move(step: int, *, wrap: bool = False) -> None:
            nonlocal selected
            pos = nav.index(selected)
            target = pos + step
            if wrap:
                target %= len(nav)
            else:
                target = max(0, min(len(nav) - 1, target))
            selected = nav[target]

        while True:
            size = self.term.size()
            w = max(20, int(size.columns))
            h = max(8, int(size.lines))
            content_w = max(16, min(max(20, int(content_width_limit)), max(16, w - 4)))
            logo = brand_logo_lines(w, h)

                                                                       
            footer_lines = wrap_plain(ANSI_RE.sub("", footer), max(12, content_w), max_lines=2)
            footer_rows = max(1, len(footer_lines))
            bottom_rows = 2 if bottom_item else 0                                   
            available_h = max(1, h - footer_rows - bottom_rows)

            block: list[tuple[str, Any, str]] = []
            for line in logo:
                block.append((center_ansi(line, w), None, "text"))

            extra_header = list(header or [])
            if extra_header:
                block.append(("", None, "text"))
                for raw in extra_header:
                    plain = ANSI_RE.sub("", str(raw))
                    for line in wrap_plain(plain, content_w, max_lines=2):
                        block.append((center_ansi(f"{cfg.SUBTITLE_COLOR}{DIM}{line}{RESET}", w), None, "text"))

            if title:
                block.append(("", None, "text"))
                title_plain = ANSI_RE.sub("", title)
                for line in wrap_plain(title_plain, content_w, max_lines=3):
                    block.append((center_ansi(f"{BOLD}{line}{RESET}", w), None, "text"))
                if spacer_after_title:
                    block.append(("", None, "text"))
            elif extra_header:
                block.append(("", None, "text"))

                                                                             
                                                                                   
            graphic: Optional[MenuGraphic] = None
            graphic_render_rows = 0
            if graphic_factory and self.term.supports_sixel():
                try:
                    graphic = graphic_factory(size)
                except Exception:
                    graphic = None
            graphic_offset: Optional[int] = None
            if graphic is not None and graphic.image is not None:
                min_item_rows = 2 if len(selectable) >= 2 else 1
                reserve = max(0, int(graphic_reserve_rows))
                max_graphic_rows = max(0, available_h - len(block) - min_item_rows - 2 - reserve)
                graphic_rows = max(0, min(int(graphic.rows), max_graphic_rows))
                if graphic_rows >= 2:
                    graphic_render_rows = graphic_rows
                    graphic_offset = len(block)
                    for _ in range(graphic_rows):
                        block.append(("", None, "graphic"))
                    block.append(("", None, "text"))
                else:
                    graphic = None

            dynamic_subtitle = subtitle_factory(size) if subtitle_factory else subtitle
            subtitle_lines: list[str] = []
            if dynamic_subtitle:
                for raw in str(dynamic_subtitle).splitlines():
                    if not raw:
                        subtitle_lines.append("")
                    else:
                        subtitle_lines.extend(wrap_plain(ANSI_RE.sub("", raw), content_w))

                                                               
            min_item_rows = 2 if len(selectable) >= 2 else 1
            subtitle_budget = max(0, available_h - len(block) - min_item_rows - 2)
            if len(subtitle_lines) > subtitle_budget:
                if subtitle_budget >= 2:
                    subtitle_lines = subtitle_lines[: subtitle_budget - 1] + ["…"]
                else:
                    subtitle_lines = subtitle_lines[:subtitle_budget]
            for line in subtitle_lines:
                rendered = f"{cfg.SUBTITLE_COLOR}{DIM}{line}{RESET}" if line else ""
                block.append((center_ansi(rendered, w) if rendered else "", None, "text"))
            if subtitle_lines:
                block.append(("", None, "text"))

                                                                                    
            def item_lines(idx: int) -> list[str]:
                item = items[idx]
                if not item.selectable:
                    plain = ANSI_RE.sub("", item.label)
                    chunks = wrap_plain(plain, max(8, content_w - 2), max_lines=2)
                    return [center_ansi(f"{cfg.SUBTITLE_COLOR}{DIM}{line}{RESET}", w) for line in chunks]

                available = max(8, content_w - 4)
                combined = item.label + (f" · {item.hint}" if item.hint else "")
                chunks = wrap_plain(combined, available, max_lines=3)
                active = selected == idx
                rendered: list[str] = []
                for n, chunk in enumerate(chunks):
                    prefix = f"{cfg.PRIMARY_COLOR}›{RESET} " if active and n == 0 else "  "
                    body = f"{BOLD}{WHITE}{chunk}{RESET}" if active else chunk
                    rendered.append(center_ansi(prefix + body, w))
                return rendered

            all_item_lines = {idx: item_lines(idx) for idx in range(len(items))}
            scroll_info_rows = 1 if len(items) > 4 else 0
            item_budget = max(1, available_h - len(block) - scroll_info_rows)

            if isinstance(selected, int):
                if selected < scroll:
                    scroll = selected
                scroll = max(0, min(scroll, len(items) - 1))
                                                                                
                                                                                   
                while True:
                    used = 0
                    end_idx = scroll
                    while end_idx < len(items):
                        need = len(all_item_lines[end_idx])
                        if used and used + need > item_budget:
                            break
                        used += min(need, item_budget)
                        end_idx += 1
                        if used >= item_budget:
                            break
                    if selected < end_idx or scroll >= selected:
                        break
                    scroll += 1

            used_rows = 0
            end = scroll
            visible_entries: list[tuple[int, list[str]]] = []
            while end < len(items) and used_rows < item_budget:
                lines_for_item = all_item_lines[end]
                remain = item_budget - used_rows
                if len(lines_for_item) > remain:
                    if not visible_entries:
                        lines_for_item = lines_for_item[:remain]
                    else:
                        break
                visible_entries.append((end, lines_for_item))
                used_rows += len(lines_for_item)
                end += 1

            for idx, lines_for_item in visible_entries:
                target = idx if items[idx].selectable else None
                for line in lines_for_item:
                    block.append((line, target, "item"))

            hidden = scroll > 0 or end < len(items)
            if hidden and scroll_info_rows:
                if isinstance(selected, int) and selected in selectable:
                    pos = selectable.index(selected) + 1
                else:
                    pos = len(selectable)
                info = f"{pos}/{len(selectable)}  ·  {scroll + 1}–{end}"
                block.append((center_ansi(f"{DIM}{info}{RESET}", w), None, "text"))

            centered_start = max(0, (available_h - len(block)) // 2)
                                                                                
                                                                                 
            top_cap = max(1, min(4, h // 9))
            start_row = min(centered_start, top_cap)
            frame = [""] * h
            hit_rows: dict[int, Any] = {}
            graphic_top_row: Optional[int] = None
            for offset, (line, target, kind) in enumerate(block):
                row = start_row + offset
                if row >= available_h or row >= h:
                    break
                frame[row] = line
                if target is not None:
                    hit_rows[row] = target
                if kind == "graphic" and graphic_top_row is None:
                    graphic_top_row = row + 1                       

            if bottom_item:
                row = max(0, h - footer_rows - 2)
                active = selected == BOTTOM
                prefix = f"{cfg.PRIMARY_COLOR}›{RESET} " if active else "  "
                label = f"{BOLD}{WHITE}{bottom_item.label}{RESET}" if active else bottom_item.label
                frame[row] = center_ansi(prefix + clip(label, max(8, content_w - 2)), w)
                hit_rows[row] = BOTTOM

            footer_start = h - footer_rows
            for i, line in enumerate(footer_lines):
                if 0 <= footer_start + i < h:
                    frame[footer_start + i] = center_ansi(f"{cfg.STATUS_COLOR}{DIM}{line}{RESET}", w)

            full_redraw = self.term.render(frame, animate=animate_first)
            animate_first = False
            if graphic is not None and graphic_top_row is not None:
                self.term.draw_sixel(
                    graphic.image,
                    top_row=graphic_top_row,
                    max_columns=max(4, min(int(graphic.columns), content_w)),
                    max_rows=max(2, graphic_render_rows),
                    graphic_key=graphic.key,
                    force=full_redraw,
                )

            event = self.term.read_event()
            if event.kind == "resize":
                continue
            if event.kind == "key":
                if event.key == "up":
                    move(-1, wrap=True)
                elif event.key == "down":
                    move(1, wrap=True)
                elif event.key == "pageup":
                    move(-max(1, item_budget // 2))
                elif event.key == "pagedown":
                    move(max(1, item_budget // 2))
                elif event.key == "home":
                    selected = nav[0]
                elif event.key == "end":
                    selected = nav[-1]
                elif event.key == "enter":
                    if selected == BOTTOM and bottom_item:
                        return bottom_item.value
                    return items[selected].value
                elif event.key == "escape" and allow_escape:
                    return None
            elif event.kind == "mouse":
                if event.key == "wheel":
                    move(-1 if event.delta > 0 else 1, wrap=False)
                elif event.key == "move" and event.y in hit_rows:
                    hovered = hit_rows[event.y]
                    if hovered in nav:
                        selected = hovered
                elif event.key == "click" and event.y in hit_rows:
                    clicked = hit_rows[event.y]
                    selected = clicked
                    if clicked == BOTTOM and bottom_item:
                        return bottom_item.value
                    if isinstance(clicked, int) and items[clicked].selectable:
                        return items[clicked].value
