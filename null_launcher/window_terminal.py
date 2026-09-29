from __future__ import annotations

import contextlib
import ctypes
import os
from pathlib import Path
import queue
import re
import sys
import time
from typing import Any, Optional

from . import config as cfg
from .config import ANSI_RE, APP_NAME, APP_VERSION
from .terminal import InputEvent
from .utils import _cmd_filter_image, _sixel_geometry


_ANSI_TOKEN_RE = re.compile(r"\x1b\[([0-9;]*)m")
_BASIC_COLORS = {
    30: "#000000", 31: "#CC6666", 32: "#B5BD68", 33: "#F0C674",
    34: "#81A2BE", 35: "#B294BB", 36: "#8ABEB7", 37: "#C5C8C6",
    90: "#737D86", 91: "#FF6B6B", 92: "#9BE564", 93: "#FFD166",
    94: "#7AA2F7", 95: "#C099FF", 96: "#62F4FF", 97: "#F5F7FA",
}


class WindowTerminal:
    """A small native Tk terminal surface for the frozen Windows launcher.

    The launcher keeps its keyboard-first terminal UI, but Windows sees a real
    application window owned by NullLauncher instead of a CMD/Windows Terminal
    host.  That gives us a stable title, taskbar identity and executable icon.
    """

    def __init__(self, mouse_enabled: bool = True):
        import tkinter as tk
        import tkinter.font as tkfont

        self.mouse_enabled = mouse_enabled
        self._tk = tk
        self._events: "queue.Queue[InputEvent]" = queue.Queue()
        # Tk can emit hundreds of <Motion> events while the pointer crosses a menu.
        # Keep only the newest position so hover selection jumps directly to the
        # item under the cursor instead of replaying every intermediate row.
        self._latest_motion: Optional[InputEvent] = None
        self._motion_pending = False
        self._closed = False
        self._last_frame: list[str] = []
        self._last_size: Optional[tuple[int, int]] = None
        self._view_key = ""
        self._rendered_view_key = ""
        self._last_graphic_key: Optional[tuple[Any, ...]] = None
        self._photo_refs: list[Any] = []
        self._image_cache: dict[tuple[Any, ...], Any] = {}
        # App settings/cache code expects the terminal backend to expose the
        # same cache name as the SIXEL console backend.  Alias it so changing
        # the news filter invalidates GUI previews too.
        self._sixel_cache = self._image_cache

        self._set_app_id()
        self.root = tk.Tk(className=APP_NAME)
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.configure(bg="#080B0E")
        self.root.minsize(760, 480)

        self.font = tkfont.Font(family="Consolas", size=11)
        self.font_bold = tkfont.Font(family="Consolas", size=11, weight="bold")
        self._char_w = max(1, int(self.font.measure("M")))
        self._line_h = max(1, int(self.font.metrics("linespace")) + 1)

        self.canvas = tk.Canvas(
            self.root,
            bg="#080B0E",
            highlightthickness=0,
            bd=0,
            takefocus=True,
        )
        self.canvas.pack(fill="both", expand=True)
        self._apply_icon()
        self._bind_events()

        width = max(900, self._char_w * 112 + 24)
        height = max(560, self._line_h * 34 + 24)
        self.root.geometry(f"{width}x{height}")
        self.root.update_idletasks()
        self.canvas.focus_set()

    def _set_app_id(self) -> None:
        if os.name != "nt":
            return
        with contextlib.suppress(Exception):
            shell32 = ctypes.windll.shell32
            shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
            shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
            shell32.SetCurrentProcessExplicitAppUserModelID("BrawliPup12.NullLauncher")

    @staticmethod
    def _asset_path(name: str) -> Optional[Path]:
        roots: list[Path] = []
        bundle = getattr(sys, "_MEIPASS", "")
        if bundle:
            roots.append(Path(bundle))
        roots.append(Path(__file__).resolve().parents[1])
        for root in roots:
            candidate = root / "assets" / name
            if candidate.is_file():
                return candidate
        return None

    def _apply_icon(self) -> None:
        icon = self._asset_path("NullLauncher.ico")
        if icon is None:
            return
        with contextlib.suppress(Exception):
            self.root.iconbitmap(default=str(icon))

    def _bind_events(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Configure>", self._on_configure)
        self.root.bind("<KeyPress>", self._on_key)
        if self.mouse_enabled:
            self.canvas.bind("<Motion>", self._on_motion)
            self.canvas.bind("<Button-1>", self._on_click)
            self.canvas.bind("<MouseWheel>", self._on_wheel)
            self.canvas.bind("<Button-4>", lambda event: self._events.put(InputEvent("mouse", "wheel", self._event_col(event), self._event_row(event), 120)))
            self.canvas.bind("<Button-5>", lambda event: self._events.put(InputEvent("mouse", "wheel", self._event_col(event), self._event_row(event), -120)))

    def _on_close(self) -> None:
        self._closed = True
        self._events.put(InputEvent("close"))

    def _on_configure(self, event: Any) -> None:
        if event.widget is self.root:
            self._events.put(InputEvent("resize"))

    def _on_key(self, event: Any) -> str:
        keys = {
            "Up": "up", "Down": "down", "Left": "left", "Right": "right",
            "Return": "enter", "KP_Enter": "enter", "Escape": "escape",
            "BackSpace": "backspace", "Prior": "pageup", "Next": "pagedown",
            "Home": "home", "End": "end", "Delete": "delete",
        }
        key = keys.get(str(event.keysym))
        if key is None:
            char = str(event.char or "")
            if char and ord(char[0]) >= 32:
                key = char
        if key:
            self._events.put(InputEvent("key", key))
        return "break"

    def _event_col(self, event: Any) -> int:
        return max(0, int(event.x) // max(1, self._char_w))

    def _event_row(self, event: Any) -> int:
        return max(0, int(event.y) // max(1, self._line_h))

    def _on_motion(self, event: Any) -> None:
        self._latest_motion = InputEvent("mouse", "move", self._event_col(event), self._event_row(event))
        if not self._motion_pending:
            self._motion_pending = True
            # Coordinates are resolved when the event is consumed; this marker
            # merely preserves ordering relative to clicks/wheel/key events.
            self._events.put(InputEvent("mouse", "move"))

    def _on_click(self, event: Any) -> None:
        self.canvas.focus_set()
        self._events.put(InputEvent("mouse", "click", self._event_col(event), self._event_row(event)))

    def _on_wheel(self, event: Any) -> None:
        self._events.put(InputEvent("mouse", "wheel", self._event_col(event), self._event_row(event), int(event.delta)))

    def _pump(self) -> None:
        if self._closed:
            raise KeyboardInterrupt
        try:
            self.root.update_idletasks()
            self.root.update()
        except self._tk.TclError as exc:
            self._closed = True
            raise KeyboardInterrupt from exc

    def _init_console(self) -> None:
        # Compatibility hook used by source-mode dependency installation code.
        return

    def restore(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            self.root.destroy()

    def clear(self) -> None:
        if self._closed:
            return
        self._last_frame = []
        self._last_size = None
        self._rendered_view_key = ""
        self._last_graphic_key = None
        self._photo_refs.clear()
        with contextlib.suppress(Exception):
            self.canvas.delete("all")
            self._pump()

    def size(self) -> os.terminal_size:
        if self._closed:
            return os.terminal_size((100, 32))
        with contextlib.suppress(Exception):
            self._pump()
        width = max(1, int(self.canvas.winfo_width()))
        height = max(1, int(self.canvas.winfo_height()))
        columns = max(20, width // max(1, self._char_w))
        rows = max(8, height // max(1, self._line_h))
        return os.terminal_size((columns, rows))

    @staticmethod
    def _parse_ansi(line: str) -> list[tuple[str, str, bool, bool]]:
        result: list[tuple[str, str, bool, bool]] = []
        color = "#D8DEE9"
        bold = False
        dim = False
        pos = 0
        for match in _ANSI_TOKEN_RE.finditer(str(line)):
            if match.start() > pos:
                result.append((line[pos:match.start()], color, bold, dim))
            params = [int(p) if p else 0 for p in match.group(1).split(";")] if match.group(1) else [0]
            i = 0
            while i < len(params):
                code = params[i]
                if code == 0:
                    color, bold, dim = "#D8DEE9", False, False
                elif code == 1:
                    bold = True
                elif code == 2:
                    dim = True
                elif code == 22:
                    bold = False
                    dim = False
                elif code == 39:
                    color = "#D8DEE9"
                elif code in _BASIC_COLORS:
                    color = _BASIC_COLORS[code]
                elif code == 38 and i + 4 < len(params) and params[i + 1] == 2:
                    r, g, b = params[i + 2:i + 5]
                    color = f"#{max(0, min(255, r)):02X}{max(0, min(255, g)):02X}{max(0, min(255, b)):02X}"
                    i += 4
                i += 1
            pos = match.end()
        if pos < len(line):
            result.append((line[pos:], color, bold, dim))
        return result

    @staticmethod
    def _dim_color(hex_color: str) -> str:
        try:
            rgb = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
            rgb = [max(0, min(255, int(v * 0.68))) for v in rgb]
            return "#" + "".join(f"{v:02X}" for v in rgb)
        except Exception:
            return hex_color

    def render(self, lines: list[str], *, animate: bool = False, delay: float = 0.014) -> bool:
        self._pump()
        size = self.size()
        cols = max(1, int(size.columns))
        rows = max(1, int(size.lines))
        frame = list(lines[:rows])
        if len(frame) < rows:
            frame.extend([""] * (rows - len(frame)))
        current_size = (cols, rows)
        view_changed = self._view_key != self._rendered_view_key
        full_redraw = bool(
            animate
            or not self._last_frame
            or self._last_size != current_size
            or view_changed
        )

        # Keep the raster layer alive during ordinary hover/selection updates.
        # Deleting the whole canvas here made news images disappear for a frame
        # on every mouse move, which looked like aggressive flickering.
        if full_redraw:
            self.canvas.delete("all")
            self._photo_refs.clear()
            self._last_graphic_key = None
        else:
            self.canvas.delete("text")

        pad_x = max(0, (int(self.canvas.winfo_width()) - cols * self._char_w) // 2)
        for row, line in enumerate(frame):
            x = pad_x
            y = row * self._line_h
            for text, color, bold, dim in self._parse_ansi(line):
                if not text:
                    continue
                fill = self._dim_color(color) if dim else color
                font = self.font_bold if bold else self.font
                self.canvas.create_text(
                    x, y, text=text, anchor="nw", fill=fill, font=font, tags=("text",)
                )
                x += int(font.measure(text))
            if animate and line:
                self._pump()
                time.sleep(max(0.0, float(delay)))

        self._last_frame = frame
        self._last_size = current_size
        self._rendered_view_key = self._view_key
        self._pump()
        return full_redraw

    def supports_sixel(self) -> bool:
        # The GUI backend can draw Pillow images directly, so callers can use the
        # same image-preview path they use for SIXEL terminals.
        return True

    def cell_pixel_size(self) -> tuple[int, int]:
        return max(1, self._char_w), max(1, self._line_h)

    def sixel_display_scale(self) -> float:
        return 1.0

    def sixel_geometry(self, image: Any, cell_columns: int, cell_rows: int) -> tuple[int, int]:
        # _sixel_geometry returns encoded pixel width/height followed by the
        # occupied terminal columns/rows.  The menu API expects only the latter.
        # v1.11.4 accidentally returned all four values, so article_graphic()
        # failed to unpack them and news covers silently disappeared.
        _, _, occupied_columns, occupied_rows = _sixel_geometry(
            image,
            max(1, int(cell_columns)),
            max(1, int(cell_rows)),
            self.cell_pixel_size(),
            1.0,
        )
        return occupied_columns, occupied_rows

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
        if image is None or max_columns < 2 or max_rows < 1 or self._closed:
            return False
        try:
            from PIL import Image, ImageTk

            cols, rows = self.sixel_geometry(image, max_columns, max_rows)
            target_w = max(1, cols * self._char_w)
            target_h = max(1, rows * self._line_h)
            draw_key = (
                graphic_key,
                int(top_row),
                target_w,
                target_h,
                cfg.NEWS_FILTER_RGB,
            )
            if not force and draw_key == self._last_graphic_key:
                return True

            key = (graphic_key, id(image), target_w, target_h, cfg.NEWS_FILTER_RGB)
            photo = self._image_cache.get(key)
            if photo is None:
                # Use the same terminal-style tint/contrast/scanline filter as
                # the SIXEL backend instead of displaying the raw article RGB.
                styled = _cmd_filter_image(image, target_w, target_h)
                photo = ImageTk.PhotoImage(styled, master=self.root)
                if len(self._image_cache) > 20:
                    self._image_cache.clear()
                self._image_cache[key] = photo

            x = max(0, (int(self.canvas.winfo_width()) - int(photo.width())) // 2)
            y = max(0, (int(top_row) - 1) * self._line_h)
            self.canvas.delete("graphic")
            self._photo_refs.clear()
            self.canvas.create_image(x, y, image=photo, anchor="nw", tags=("graphic",))
            self._photo_refs.append(photo)
            self._last_graphic_key = draw_key
            self._pump()
            return True
        except Exception:
            return False

    def prompt(self, label: str, default: str = "") -> str:
        self._view_key = "prompt:" + ANSI_RE.sub("", str(label))
        value = str(default or "")
        first = True
        while True:
            size = self.size()
            w = max(20, int(size.columns))
            h = max(8, int(size.lines))
            content_w = max(16, min(120, w - 4))
            from .config import BOLD, DIM, RESET, tr
            from .utils import _tail_cells, brand_logo_lines, center_ansi, visible_len, wrap_plain

            logo = brand_logo_lines(w, h)
            label_lines = wrap_plain(label, max(12, content_w - 2), max_lines=max(1, h // 5))
            help_lines = wrap_plain(tr("prompt_help"), max(12, content_w), max_lines=2)
            input_w = max(8, content_w - 4)
            prompt_text = "> " + _tail_cells(value, input_w)
            content: list[str] = list(logo) + [""]
            content.extend(f"{BOLD}{line}{RESET}" for line in label_lines)
            content.extend(["", prompt_text, ""])
            content.extend(f"{cfg.SUBTITLE_COLOR}{DIM}{line}{RESET}" for line in help_lines)
            frame = [""] * h
            start = max(0, (h - len(content)) // 2)
            for i, line in enumerate(content):
                row = start + i
                if row < h:
                    frame[row] = center_ansi(line, w)
            self.render(frame, animate=first)
            first = False
            event = self.read_event()
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
        while True:
            self._pump()
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                time.sleep(0.01)
                continue
            if event.kind == "close":
                raise KeyboardInterrupt
            if event.kind == "mouse" and event.key == "move":
                latest = self._latest_motion
                self._latest_motion = None
                self._motion_pending = False
                if latest is not None:
                    return latest
                continue
            return event

    def show_fatal(self, title: str, message: str) -> None:
        if self._closed:
            return
        with contextlib.suppress(Exception):
            from tkinter import messagebox
            messagebox.showerror(title, message, parent=self.root)
