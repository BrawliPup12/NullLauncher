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
from .config import APP_NAME, APP_VERSION, BOLD, DIM, LANGUAGES, MC_NAME_RE, RED, RESET, SPLASH_LINES, YELLOW, apply_runtime_preferences, normalize_user_color, section_label, tr
from .utils import brand_logo_lines, center_ansi, centered_splash_progress, clean_markup, clip, decode_article_image, normalize_version_type, now_iso, offline_uuid, proxy_game_arguments, proxy_jvm_arguments, proxy_label, recommended_ram_mb, safe_int, slug, tail_text, valid_proxy_host, wrap_plain
from .updater import check_github_update, download_github_update, running_artifact_path, spawn_update_replacer
from .state import StateStore
from .terminal import Menu, MenuGraphic, MenuItem, Terminal
from .news import NEWS_LIMIT, _article_from_url, _download_article_image_bytes, _news_translation_ident, _news_translation_signature
from .catalog import LauncherData, VersionEntry

                                                                             

class NullLauncher:
    def __init__(self, store: StateStore, term: Terminal, mll: Any, logger: logging.Logger):
        self.store = store
        apply_runtime_preferences(self.store.settings)
        self.term = term
        self.menu = Menu(term)
        self.mll = mll
        self.log = logger
        self.data = LauncherData(store, logger)
        self.running = True
        self._news_image_cache: dict[str, Any] = {}

    def splash(self) -> None:
        import random

        tagline = random.choice(SPLASH_LINES.get(cfg.CURRENT_LANGUAGE, SPLASH_LINES["en"]))
        state = {"text": tr("preparing"), "value": 0, "max": 5}
        update_state: dict[str, Any] = {
            "active": bool(self.store.settings.get("auto_update", True)),
            "text": tr("checking_updates") if self.store.settings.get("auto_update", True) else "",
            "ready": None,
        }
        state_lock = threading.Lock()
        errors: list[BaseException] = []

        def on_progress(value: int, maximum: int, text: str) -> None:
            with state_lock:
                state.update(text=str(text), value=int(value), max=max(1, int(maximum)))

        def preload_worker() -> None:
            try:
                self.data.preload(self.mll, on_progress)
            except BaseException as exc:
                errors.append(exc)

        def update_worker() -> None:
            if not self.store.settings.get("auto_update", True):
                return
            try:
                info = check_github_update()
                if info is None:
                    with state_lock:
                        update_state["active"] = False
                        update_state["text"] = ""
                    return
                with state_lock:
                    update_state["text"] = tr("update_downloading", version=info.version)
                downloaded = download_github_update(info, running_artifact_path())
                with state_lock:
                    update_state["ready"] = (info, downloaded)
                    update_state["text"] = tr("update_restarting", version=info.version)
            except Exception as exc:
                self.log.warning("Automatic GitHub update check failed: %s", exc)
                with state_lock:
                    update_state["active"] = False
                    update_state["text"] = ""

        def render_splash(*, animate: bool = False, warning: str = "") -> None:
            size = self.term.size()
            w = max(20, int(size.columns))
            h = max(8, int(size.lines))
            with state_lock:
                value = int(state["value"])
                maximum = int(state["max"])
                status = str(state["text"])
                update_text = str(update_state.get("text") or "") if update_state.get("active") or update_state.get("ready") else ""
            logo = brand_logo_lines(w, h)
            content_w = max(16, min(140, w - 4))
            activity = status
            if update_text:
                activity = f"{activity}  ·  {update_text}" if activity else update_text
            activity_lines = wrap_plain(activity, max(12, content_w), max_lines=3)
            content = [center_ansi(line, w) for line in logo] + [""]
            content.extend(center_ansi(f"{cfg.SUBTITLE_COLOR}{DIM}{line}{RESET}", w) for line in wrap_plain(tagline, content_w, max_lines=2))
            content.append("")
                                                                                 
                                                                                 
            content.append(centered_splash_progress(w, value, maximum))
            for line in activity_lines:
                content.append(center_ansi(f"{cfg.SUBTITLE_COLOR}{DIM}{line}{RESET}", w))
            if warning:
                content.extend(["", center_ansi(f"{YELLOW}{clip(warning, content_w)}{RESET}", w)])

            frame = [""] * h
            start_row = max(0, (h - len(content)) // 2)
            for i, line in enumerate(content):
                row = start_row + i
                if row < h:
                    frame[row] = line
            self.term.render(frame, animate=animate)

        self.term.clear()
        render_splash(animate=True)
        preload_thread = threading.Thread(target=preload_worker, name="NullPreloadUI", daemon=False)
        preload_thread.start()
        update_thread: Optional[threading.Thread] = None
        if self.store.settings.get("auto_update", True):
            update_thread = threading.Thread(target=update_worker, name="NullUpdate", daemon=False)
            update_thread.start()

        last_signature: Optional[tuple[Any, ...]] = None
        while preload_thread.is_alive() or (update_thread is not None and update_thread.is_alive()):
            size = self.term.size()
            with state_lock:
                signature = (
                    size.columns, size.lines, state["value"], state["max"], state["text"],
                    update_state.get("text"), bool(update_state.get("ready")),
                )
            if signature != last_signature:
                render_splash()
                last_signature = signature
            time.sleep(0.04)
        preload_thread.join()
        if update_thread is not None:
            update_thread.join()
        if errors:
            raise errors[0]

        with state_lock:
            ready_update = update_state.get("ready")
        if isinstance(ready_update, tuple) and len(ready_update) == 2:
            info, downloaded = ready_update
            render_splash()
            time.sleep(0.20)
            self.term.restore()
            spawn_update_replacer(Path(downloaded), running_artifact_path())
            raise SystemExit(0)

        warning = tr("cache_warning") if self.data.network_errors else ""
        render_splash(warning=warning)
        time.sleep(0.20)
        self.term._view_key = "__splash__"

    def run(self) -> int:
        self.splash()
        while self.running:
            apply_runtime_preferences(self.store.settings)
            account = self.store.account()
            proxy = self.store.proxy_profile()
            selected = self.store.data.get("selected_version") or tr("not_selected")
            acc_name = account["name"] if account else tr("not_selected")
            proxy_name = proxy["name"] if proxy else tr("none")
            header = [
                f"{cfg.SUBTITLE_COLOR}{DIM}NullLauncher v{APP_VERSION}{RESET}",
                f"{cfg.SUBTITLE_COLOR}{DIM}{tr('account')}:{RESET} {acc_name}    {cfg.SUBTITLE_COLOR}{DIM}{tr('version')}:{RESET} {selected}",
                f"{cfg.SUBTITLE_COLOR}{DIM}{tr('proxy')}:{RESET} {proxy_name}",
            ]
            items = [
                MenuItem(tr("play"), "play"),
                MenuItem(tr("versions"), "versions"),
                MenuItem(tr("accounts"), "accounts"),
                MenuItem(tr("proxies"), "proxies"),
                MenuItem(tr("settings"), "settings"),
                MenuItem(tr("exit"), "exit"),
            ]
            action = self.menu.choose(
                "", items, header=header, allow_escape=False,
                footer=tr("footer_main"),
                bottom_item=MenuItem(tr("news"), "news"),
                view_key="main",
            )
            if action == "play": self.play()
            elif action == "versions": self.versions_screen()
            elif action == "accounts": self.accounts_screen()
            elif action == "proxies": self.proxies_screen()
            elif action == "settings": self.settings_screen()
            elif action == "news": self.news_screen()
            elif action == "exit": self.running = False
        return 0

    def manual_update_check(self) -> None:
        """Check GitHub Releases on demand; install a newer release immediately."""
        def worker(callbacks: dict[str, Callable]) -> Any:
            callbacks["setStatus"](tr("checking_updates"))
            info = check_github_update(timeout=7.0)
            if info is None:
                return None
            callbacks["setStatus"](tr("update_downloading", version=info.version))
            downloaded = download_github_update(info, running_artifact_path(), timeout=20.0)
            callbacks["setStatus"](tr("update_installing", version=info.version))
            return info, downloaded

        try:
            result = self.progress_task(tr("settings_updates"), worker)
        except Exception as exc:
            self.log.warning("Manual GitHub update check failed: %s", exc)
            self.message(tr("settings_updates"), tr("update_failed", error=clean_markup(exc)), error=True)
            return
        if not result:
            self.message(tr("settings_updates"), tr("update_none"))
            return
        info, downloaded = result
        self.term.restore()
        spawn_update_replacer(Path(downloaded), running_artifact_path())
        raise SystemExit(0)

                                                                             

    @staticmethod
    def _news_field(entry: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = entry.get(key)
            if value:
                return clean_markup(value)
        return ""

    def _localized_news_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Return the startup-pretranslated article without blocking the UI.

        All supported languages are prepared in parallel during the splash
        screen.  If one translation failed there, the canonical English text is
        shown immediately instead of making the user wait inside the news view.
        """
        language = str(self.store.settings.get("language") or "en")
        if language == "en":
            return dict(entry)

        ident = _news_translation_ident(entry)
        signature = _news_translation_signature(entry)
        root = self.store.cache.get("news_translations", {})
        lang_cache = root.get(language, {}) if isinstance(root, dict) else {}
        cached = lang_cache.get(ident) if isinstance(lang_cache, dict) else None
        merged = dict(entry)
        if isinstance(cached, dict) and cached.get("signature") == signature:
            for field in ("title", "description", "category"):
                value = cached.get(field)
                if value:
                    merged[field] = clean_markup(value)
        return merged

    def _article_url(self, entry: dict[str, Any]) -> str:
        for key in ("readMoreLink", "link", "url", "articleUrl"):
            raw = entry.get(key)
            if isinstance(raw, str) and raw.startswith(("http://", "https://")):
                return raw
        return ""

    def _resolve_news_image_url(self, entry: dict[str, Any]) -> str:
        """Return the real image advertised by the official article page."""
        article_url = self._article_url(entry)
        image_url = str(entry.get("image") or "").strip()
        origin = str(entry.get("image_origin") or "").strip().lower()

                                                                                 
                                                                                  
                                                             
        if article_url and "minecraft.net/" in article_url.lower() and "/article/" in article_url.lower() and origin != "page":
            try:
                fresh = _article_from_url(article_url)
                official = str(fresh.get("image") or "").strip()
                if official:
                    image_url = official
                    entry["image"] = official
                    entry["image_origin"] = "page"
            except Exception as exc:
                self.log.debug("Article image metadata refresh failed for %s: %s", article_url, exc)

        if image_url and article_url:
            image_url = urljoin(article_url, image_url)
        return image_url if image_url.startswith(("http://", "https://")) else ""

    def _load_news_image(self, entry: dict[str, Any]) -> Optional[Any]:
        """Decode a real article cover once and reuse the original pixels on resize."""
        image_url = self._resolve_news_image_url(entry)
        if not image_url:
            return None
        cached = self._news_image_cache.get(image_url)
        if cached is not None:
            return cached

        cache_dir = self.store.base / "news-images"
        cache_path = cache_dir / (hashlib.sha256(image_url.encode("utf-8")).hexdigest() + ".img")
        raw: Optional[bytes] = None
        try:
            if cache_path.exists() and cache_path.stat().st_size > 0:
                raw = cache_path.read_bytes()
                image = decode_article_image(raw)
                self._news_image_cache[image_url] = image
                return image
        except Exception:
            with contextlib.suppress(OSError):
                cache_path.unlink()

        try:
            raw = _download_article_image_bytes(image_url)
            image = decode_article_image(raw)
            self._news_image_cache[image_url] = image
            try:
                cache_dir.mkdir(parents=True, exist_ok=True)
                tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
                tmp.write_bytes(raw)
                os.replace(tmp, cache_path)
            except OSError:
                pass
            return image
        except Exception as exc:
            self.log.warning("News image download/decode failed: %s (%s)", image_url, exc)
            return None

    def news_screen(self) -> None:
        if not self.data.news:
            self.message(tr("news"), tr("news_unavailable"), error=bool(self.data.network_errors))
            return
        while True:
            items: list[MenuItem] = []
            for i, entry in enumerate(self.data.news[:NEWS_LIMIT]):
                display_entry = self._localized_news_entry(entry)
                title = self._news_field(display_entry, "title") or f"{tr('article')} {i + 1}"
                category = self._news_field(display_entry, "category", "tag", "type")
                date = self._news_field(entry, "date", "publishDate", "published", "publishedAt")
                items.append(MenuItem(title, ("article", i), hint=" · ".join(x for x in (category, date) if x)))
            items.append(MenuItem(tr("back"), ("back", -1)))
            action = self.menu.choose(tr("news"), items, view_key="news-list", spacer_after_title=True)
            if not action or action[0] == "back": return
            if action[0] == "article": self.news_article_screen(action[1])

    def news_article_screen(self, index: int) -> None:
        if not (0 <= index < len(self.data.news)):
            return
        base_entry = self.data.news[index]
        language = str(self.store.settings.get("language") or "en")
        entry = self._localized_news_entry(base_entry) if language != "en" else dict(base_entry)

        title = self._news_field(entry, "title") or "Minecraft News"
        body = self._news_field(entry, "text", "description", "summary", "excerpt")
        category = self._news_field(entry, "category", "tag", "type")
        date = self._news_field(entry, "date", "publishDate", "published", "publishedAt")
                                                                                
                                                                                 
                                                                                 
        url = self._article_url(base_entry) or self._article_url(entry)
        real_image = self._load_news_image(entry)
        meta = " · ".join(x for x in (category, date) if x)
        items = ([MenuItem(tr("open_browser"), "open")] if url else []) + [MenuItem(tr("back_news"), "back")]

        def article_graphic(size: os.terminal_size) -> Optional[MenuGraphic]:
            if real_image is None or not self.term.supports_sixel():
                return None
            columns, rows = max(20, int(size.columns)), max(8, int(size.lines))
            logo_rows = len(brand_logo_lines(columns, rows))
                                                                           
                                                                                
            max_rows = max(3, rows - logo_rows - 11)
            requested_rows = max(3, min(36, max_rows, int(rows * 0.54)))
            requested_columns = max(20, min(220, columns - 6))
                                                                                
                                                                               
                                                
            actual_columns, actual_rows = self.term.sixel_geometry(
                real_image, requested_columns, requested_rows
            )
            return MenuGraphic(
                image=real_image,
                key=f"news-image:{index}:{self._news_field(entry, 'image')}:{cfg.CURRENT_LANGUAGE}",
                rows=max(2, actual_rows),
                columns=max(4, requested_columns),
            )

        def article_subtitle(size: os.terminal_size) -> str:
            lines: list[str] = []
            if real_image is not None and not self.term.supports_sixel(): lines.append(tr("sixel_required"))
            elif real_image is None: lines.append(tr("no_cover"))
            if meta: lines.append(meta)
            lines.append(body or tr("no_description"))
            return "\n".join(lines)

        while True:
            action = self.menu.choose(
                title,
                items,
                subtitle_factory=article_subtitle,
                graphic_factory=article_graphic,
                view_key=f"news-article:{index}:{cfg.CURRENT_LANGUAGE}",
                content_width_limit=220,
                graphic_reserve_rows=5,
            )
            if not action or action == "back": return
            if action == "open" and url:
                try:
                    if not webbrowser.open(url): raise RuntimeError("The system did not confirm opening the browser")
                except Exception as exc:
                    self.message(tr("browser"), f"{exc}\n\n{url}", error=True)

                                                                            

    def message(self, title: str, text: str, *, error: bool = False) -> None:
        items = [MenuItem(tr("back"), "back")]
        color = RED if error else cfg.PRIMARY_COLOR
                                                                               
        self.menu.choose(
            f"{color}{title}{RESET}",
            items,
            subtitle=str(text),
            allow_escape=True,
            content_width_limit=120,
        )

    def confirm(self, title: str, text: str) -> bool:
        result = self.menu.choose(title, [MenuItem(tr("yes"), True), MenuItem(tr("no"), False)], subtitle=text)
        return bool(result)

    def progress_task(self, title: str, fn: Callable[[dict[str, Callable]], Any]) -> Any:
        q: queue.Queue[tuple[str, Any]] = queue.Queue()
        state = {"status": tr("preparing"), "progress": 0, "max": 0, "done": False, "error": None, "result": None}

        def set_status(value: Any) -> None:
            q.put(("status", str(value)))

        def set_progress(value: Any) -> None:
            q.put(("progress", int(value)))

        def set_max(value: Any) -> None:
            q.put(("max", int(value)))

        callbacks = {"setStatus": set_status, "setProgress": set_progress, "setMax": set_max}

        def worker() -> None:
            try:
                result = fn(callbacks)
                q.put(("result", result))
            except BaseException as exc:
                q.put(("error", exc))
            finally:
                q.put(("done", True))

        thread = threading.Thread(target=worker, name="NullTask", daemon=False)
        thread.start()
        self.term._view_key = "progress:" + clean_markup(title)
        first_frame = True
        while not state["done"]:
            try:
                while True:
                    key, value = q.get_nowait()
                    state[key] = value
            except queue.Empty:
                pass
            size = self.term.size()
            w = max(20, int(size.columns))
            h = max(8, int(size.lines))
            content_w = max(16, min(140, w - 4))
            bar_w = max(8, min(60, content_w - 12))
            maxv = int(state["max"] or 0)
            prog = int(state["progress"] or 0)
            if maxv > 0:
                ratio = max(0.0, min(1.0, prog / maxv))
                done = int(bar_w * ratio)
                bar = "█" * done + "░" * (bar_w - done)
                metric = f"{prog}/{maxv}"
            else:
                phase = int(time.monotonic() * 10) % bar_w
                bar = "░" * phase + "█" + "░" * max(0, bar_w - phase - 1)
                metric = "…"
            lines = [""] * h
            content: list[str] = brand_logo_lines(w, h) + [""]
            content.extend(f"{BOLD}{line}{RESET}" for line in wrap_plain(title, content_w, max_lines=2))
            content.append("")
            content.append(f"{cfg.PRIMARY_COLOR}{bar}{RESET}  {metric}")
            content.extend(f"{DIM}{line}{RESET}" for line in wrap_plain(str(state['status']), content_w, max_lines=2))
            if h >= 16:
                content.extend(["", f"{cfg.SUBTITLE_COLOR}{DIM}{tr("do_not_close")}{RESET}"])
            start = max(0, (len(lines) - len(content)) // 2)
            for i, line in enumerate(content):
                if start + i < len(lines):
                    lines[start + i] = center_ansi(line, w)
            self.term.render(lines, animate=first_frame)
            first_frame = False
            time.sleep(0.05)
        thread.join()
        while not q.empty():
            key, value = q.get_nowait()
            state[key] = value
        if state["error"] is not None:
            raise state["error"]
        return state["result"]

                                                                            

    def accounts_screen(self) -> None:
        while True:
            current = self.store.data.get("selected_account")
            items = [MenuItem(tr("create_account"), ("create", None))]
            if self.store.data["accounts"]:
                items.append(MenuItem(section_label(tr("account_section")), selectable=False))
            for account in self.store.data["accounts"]:
                mark = "✓ " if account["name"] == current else "  "
                items.append(MenuItem(mark + account["name"], ("account", account["name"])))
            items.append(MenuItem(tr("back"), ("back", None)))
            action = self.menu.choose(tr("accounts"), items, spacer_after_title=True, view_key="accounts")
            if not action or action[0] == "back":
                return
            if action[0] == "create":
                self.create_account()
            else:
                self.account_detail(action[1])

    def create_account(self) -> None:
        name = self.term.prompt("Имя offline-аккаунта (3–16 символов: A-Z, 0-9, _)")
        if not MC_NAME_RE.fullmatch(name):
            self.message("Некорректное имя", "Имя должно содержать 3–16 латинских букв, цифр или символ _. ", error=True)
            return
        if any(a["name"].lower() == name.lower() for a in self.store.data["accounts"]):
            self.message("Аккаунт уже есть", f"Профиль {name} уже создан.", error=True)
            return
        account = {"name": name, "uuid": offline_uuid(name), "type": "offline"}
        self.store.data["accounts"].append(account)
        self.store.data["selected_account"] = name
        self.store.save()

    def account_detail(self, name: str) -> None:
        while True:
            current = self.store.data.get("selected_account") == name
            items = []
            if not current:
                items.append(MenuItem(tr("select_launch"), "select"))
            items.extend([MenuItem(tr("delete"), "delete"), MenuItem(tr("back"), "back")])
            action = self.menu.choose(name, items, subtitle=f"Offline UUID: {offline_uuid(name)}")
            if not action or action == "back":
                return
            if action == "select":
                self.store.data["selected_account"] = name
                self.store.save()
                return
            if action == "delete":
                if self.confirm("Удалить аккаунт", f"Удалить offline-профиль {name}?"):
                    self.store.data["accounts"] = [a for a in self.store.data["accounts"] if a["name"] != name]
                    if self.store.data.get("selected_account") == name:
                        self.store.data["selected_account"] = self.store.data["accounts"][0]["name"] if self.store.data["accounts"] else None
                    self.store.save()
                    return

                                                                             

    def proxies_screen(self) -> None:
        while True:
            current = self.store.data.get("selected_proxy")
            direct_mark = "✓ " if current is None else "  "
            items: list[MenuItem] = [
                MenuItem(tr("create_proxy"), ("create", None)),
                MenuItem(direct_mark + tr("direct"), ("direct", None)),
            ]
            if self.store.data["proxy_profiles"]:
                items.append(MenuItem(section_label(tr("proxy_section")), selectable=False))
            for profile in self.store.data["proxy_profiles"]:
                mark = "✓ " if profile["id"] == current else "  "
                hint = f"{proxy_label(profile)} · {profile['host']}:{profile['port']}"
                items.append(MenuItem(mark + profile["name"], ("proxy", profile["id"]), hint=hint))
            items.append(MenuItem(tr("back"), ("back", None)))
            action = self.menu.choose(tr("proxies"), items, spacer_after_title=True, view_key="proxies")
            if not action or action[0] == "back":
                return
            if action[0] == "create":
                self.create_proxy_profile()
            elif action[0] == "direct":
                self.store.data["selected_proxy"] = None
                self.store.save()
            elif action[0] == "proxy":
                self.proxy_detail(action[1])

    def create_proxy_profile(self) -> None:
        name = clean_markup(self.term.prompt("Название прокси-профиля"))[:32]
        if not name:
            self.message("Некорректное имя", "Введите название прокси-профиля.", error=True)
            return
        if any(p["name"].lower() == name.lower() for p in self.store.data["proxy_profiles"]):
            self.message("Профиль уже есть", f"Прокси-профиль {name} уже существует.", error=True)
            return

        protocol = self.menu.choose(
            tr("type_proxy"),
            [
                MenuItem("SOCKS5", "socks5"),
                MenuItem("SOCKS4", "socks4"),
                MenuItem("Назад", None),
            ],
        )
        if protocol not in ("socks4", "socks5"):
            return

        host = self.term.prompt("Хост или IP прокси").strip()
        if not valid_proxy_host(host):
            self.message("Некорректный хост", "Используйте hostname, IPv4 или IPv6 без пробелов.", error=True)
            return
        raw_port = self.term.prompt("Порт прокси", "1080")
        try:
            port = int(raw_port)
        except ValueError:
            port = 0
        if not 1 <= port <= 65535:
            self.message("Некорректный порт", "Порт должен быть числом от 1 до 65535.", error=True)
            return

        profile = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "host": host,
            "port": port,
            "protocol": protocol,
        }
        profile["version"] = 4 if protocol == "socks4" else 5
        self.store.data["proxy_profiles"].append(profile)
        self.store.data["selected_proxy"] = profile["id"]
        self.store.save()

    def proxy_detail(self, profile_id: str) -> None:
        while True:
            profile = next((p for p in self.store.data["proxy_profiles"] if p.get("id") == profile_id), None)
            if not profile:
                return
            current = self.store.data.get("selected_proxy") == profile_id
            items: list[MenuItem] = []
            if not current:
                items.append(MenuItem(tr("select_launch"), "select"))
            else:
                items.append(MenuItem(tr("disable_proxy"), "disable"))
            items.extend([MenuItem(tr("delete"), "delete"), MenuItem(tr("back"), "back")])
            subtitle = f"{proxy_label(profile)} · {profile['host']}:{profile['port']}"
            action = self.menu.choose(profile["name"], items, subtitle=subtitle)
            if not action or action == "back":
                return
            if action == "select":
                self.store.data["selected_proxy"] = profile_id
                self.store.save()
                return
            if action == "disable":
                self.store.data["selected_proxy"] = None
                self.store.save()
                return
            if action == "delete":
                if self.confirm("Удалить прокси", f"Удалить прокси-профиль {profile['name']}?"):
                    self.store.data["proxy_profiles"] = [p for p in self.store.data["proxy_profiles"] if p.get("id") != profile_id]
                    if self.store.data.get("selected_proxy") == profile_id:
                        self.store.data["selected_proxy"] = None
                    self.store.save()
                    return

                                                                             

    def versions_screen(self) -> None:
        filter_kind = "all"
        query = ""
        filter_order = ["all", "vanilla", "fabric", "forge", "neoforge", "quilt"]
        while True:
            filter_names = {"all": tr("all"), "vanilla": "Vanilla", "fabric": "Fabric", "forge": "Forge", "neoforge": "NeoForge", "quilt": "Quilt"}
            catalog = self.data.build_catalog(filter_kind, query)
            installed_entries = [e for e in catalog if e.installed]
            available_entries = [e for e in catalog if not e.installed]
            items: list[MenuItem] = [MenuItem(f"{tr('search')}: {query or tr('none')}", ("search", None)), MenuItem(f"{tr('filter')}: {filter_names[filter_kind]}", ("filter", None))]
            if installed_entries:
                items.append(MenuItem(section_label(tr("installed")), selectable=False))
                for entry in installed_entries:
                    selected = "★" if entry.selected else " "
                    items.append(MenuItem(f"✓{selected} {entry.label}", ("version", entry), hint=normalize_version_type(entry.version_type)))
            if available_entries:
                items.append(MenuItem(section_label(tr("available")), selectable=False))
                for entry in available_entries:
                    items.append(MenuItem(f"   {entry.label}", ("version", entry), hint=normalize_version_type(entry.version_type)))
            items.append(MenuItem(tr("back"), ("back", None)))
            action = self.menu.choose(tr("versions"), items, subtitle=tr("catalog_summary", count=len(catalog)), spacer_after_title=True, view_key="versions")
            if not action or action[0] == "back": return
            if action[0] == "search": query = self.term.prompt(tr("search"), query)
            elif action[0] == "filter": filter_kind = filter_order[(filter_order.index(filter_kind) + 1) % len(filter_order)]
            elif action[0] == "version":
                self.version_detail(action[1])
                self.data.refresh_installed(self.mll)

    def version_detail(self, entry: VersionEntry) -> None:
        while True:
                                                         
            catalog = self.data.build_catalog("all", "")
            refreshed = next((e for e in catalog if e.key == entry.key or (entry.installed_id and e.installed_id == entry.installed_id)), entry)
            entry = refreshed
            subtitle = f"{tr('source')}: {entry.loader_id or entry.kind} · Minecraft: {entry.mc_version}"
            items: list[MenuItem] = []
            if entry.installed:
                if self.store.data.get("selected_version") != entry.installed_id:
                    items.append(MenuItem(tr("select_launch"), "select"))
                items.append(MenuItem(tr("version_settings"), "settings"))
                items.append(MenuItem(tr("delete"), "delete"))
            else:
                items.append(MenuItem(tr("download"), "install"))
            items.append(MenuItem(tr("back"), "back"))
            action = self.menu.choose(entry.label, items, subtitle=subtitle)
            if not action or action == "back":
                return
            if action == "select":
                self.store.data["selected_version"] = entry.installed_id
                self.store.save()
                return
            if action == "install":
                if self.install_version(entry):
                    return
            elif action == "settings":
                self.version_settings_screen(entry.installed_id)
            elif action == "delete":
                if self.delete_version(entry):
                    return

    def install_version(self, entry: VersionEntry) -> bool:
        if entry.loader_id == "forge":
            if not self.confirm(
                "Установка Forge",
                "Разработчики Forge просят не автоматизировать установку без поддержки проекта, "
                "поскольку проект финансируется рекламой на странице загрузки. Продолжить автоматическую установку?",
            ):
                return False
        mc_dir = str(self.store.minecraft_dir)
        try:
            if entry.kind == "vanilla":
                def task(cb: dict[str, Callable]) -> str:
                    self.mll.install.install_minecraft_version(entry.mc_version, mc_dir, callback=cb)
                    return entry.mc_version
            elif entry.kind == "loader":
                def task(cb: dict[str, Callable]) -> str:
                    loader = self.mll.mod_loader.get_mod_loader(entry.loader_id)
                    java = self.store.settings.get("custom_java_path") or None
                    return str(loader.install(entry.mc_version, mc_dir, callback=cb, java=java))
            else:
                self.message("Нельзя скачать", "Локальная версия уже должна существовать на диске.", error=True)
                return False
            installed_id = str(self.progress_task(f"Установка {entry.label}", task))
            self.store.data["managed_versions"][entry.key] = {
                "kind": entry.kind,
                "loader_id": entry.loader_id,
                "mc_version": entry.mc_version,
                "installed_id": installed_id,
                "installed_at": now_iso(),
            }
            self.store.data["selected_version"] = installed_id
            self.store.save()
            self.data.refresh_installed(self.mll)
            self.message("Установка завершена", f"{entry.label} установлена как {installed_id} и выбрана для запуска.")
            return True
        except Exception as exc:
            self.log.exception("Install failed: %s", entry.key)
            self.message("Ошибка установки", f"{type(exc).__name__}: {exc}\n\nПодробности записаны в null_launcher.log.", error=True)
            return False

    def delete_version(self, entry: VersionEntry) -> bool:
        vid = entry.installed_id
        if not vid:
            return False
        if not self.confirm("Удалить версию", f"Удалить профиль версии {vid}? Общие assets/libraries останутся на месте."):
            return False
        versions_root = (self.store.minecraft_dir / "versions").resolve()
        target = (versions_root / vid).resolve()
        try:
            if target.parent != versions_root:
                raise RuntimeError("Небезопасный путь версии")
            if target.exists():
                shutil.rmtree(target)
            for key, item in list(self.store.data["managed_versions"].items()):
                if isinstance(item, dict) and item.get("installed_id") == vid:
                    self.store.data["managed_versions"].pop(key, None)
            self.store.data["version_settings"].pop(vid, None)
            if self.store.data.get("selected_version") == vid:
                self.store.data["selected_version"] = None
            self.store.save()
            self.data.refresh_installed(self.mll)
            return True
        except Exception as exc:
            self.log.exception("Delete failed: %s", vid)
            self.message("Не удалось удалить", str(exc), error=True)
            return False

    def version_settings_screen(self, installed_id: str) -> None:
        while True:
            cfg = self.store.version_config(installed_id)
            yn = lambda v: tr("yes") if v else tr("no")
            items = [
                MenuItem(section_label(tr("version_memory")), selectable=False),
                MenuItem(f"{tr('ram_min')}: {cfg['min_ram_mb']} MB", "min_ram"),
                MenuItem(f"{tr('ram_max')}: {cfg['max_ram_mb']} MB", "max_ram"),
                MenuItem(section_label(tr("version_window")), selectable=False),
                MenuItem(f"{tr('custom_resolution')}: {yn(cfg['custom_resolution'])}", "resolution_toggle"),
                MenuItem(f"{tr('window_size')}: {cfg['resolution_width']}×{cfg['resolution_height']}", "resolution"),
                MenuItem(section_label(tr("version_files")), selectable=False),
                MenuItem(f"{tr('separate_dir')}: {yn(cfg['separate_game_dir'])}", "separate"),
                MenuItem(section_label(tr("version_actions")), selectable=False),
                MenuItem(tr("reset_defaults"), "reset"), MenuItem(tr("back"), "back"),
            ]
            action = self.menu.choose(f"{tr('version_settings')} · {installed_id}", items, spacer_after_title=True, view_key=f"version-settings:{installed_id}")
            if not action or action == "back": return
            if action in ("min_ram", "max_ram"):
                key = "min_ram_mb" if action == "min_ram" else "max_ram_mb"
                raw = self.term.prompt(tr("ram_min") if action == "min_ram" else tr("ram_max"), str(cfg[key]))
                cfg[key] = safe_int(raw, cfg[key], 256 if key == "min_ram_mb" else 512, 131072)
                if cfg["max_ram_mb"] < cfg["min_ram_mb"]: cfg["max_ram_mb"] = cfg["min_ram_mb"]
            elif action == "resolution_toggle": cfg["custom_resolution"] = not cfg["custom_resolution"]
            elif action == "resolution":
                w = self.term.prompt(tr("window_size") + " · width", str(cfg["resolution_width"]))
                h = self.term.prompt(tr("window_size") + " · height", str(cfg["resolution_height"]))
                cfg["resolution_width"] = safe_int(w, 1280, 320, 16384); cfg["resolution_height"] = safe_int(h, 720, 240, 16384)
            elif action == "separate": cfg["separate_game_dir"] = not cfg["separate_game_dir"]
            elif action == "reset":
                if self.confirm(tr("reset_defaults"), tr("version_reset_confirm")): self.store.reset_version_settings(installed_id)
                continue
            self.store.data["version_settings"][installed_id] = cfg; self.store.save()

                                                                             

    def settings_screen(self) -> None:
        while True:
            apply_runtime_preferences(self.store.settings)
            s = self.store.settings
            yn = lambda v: tr("yes") if v else tr("no")
            lang_name = LANGUAGES.get(s.get("language", "en"), LANGUAGES["en"])["name"]
            items = [
                MenuItem(section_label(tr("settings_paths")), selectable=False),
                MenuItem(f"{tr('minecraft_dir')}: {s['minecraft_dir']}", "mc_dir"),
                MenuItem(f"{tr('java_manual')}: {s['custom_java_path'] or tr('auto_runtime')}", "java"),
                MenuItem(section_label(tr("settings_catalog")), selectable=False),
                MenuItem(f"{tr('show_snapshots')}: {yn(s['show_snapshots'])}", "snapshots"), MenuItem(f"{tr('show_old')}: {yn(s['show_old_versions'])}", "old_versions"), MenuItem(f"{tr('load_news')}: {yn(s['news_enabled'])}", "news"),
                MenuItem(section_label(tr("settings_interface")), selectable=False),
                MenuItem(f"{tr('language')}: {lang_name}", "language"),
                MenuItem(f"{tr('primary_color')}: {s['theme_primary']}", "color_primary"), MenuItem(f"{tr('subtitle_color')}: {s['theme_subtitle']}", "color_subtitle"), MenuItem(f"{tr('status_color')}: {s['theme_status']}", "color_status"), MenuItem(f"{tr('news_filter_color')}: {s['news_filter_color']}", "color_news_filter"),
                MenuItem(f"{tr('mouse_menu')}: {yn(s['mouse_enabled'])}", "mouse", hint=tr("after_restart")),
                MenuItem(section_label(tr("settings_launch")), selectable=False),
                MenuItem(f"{tr('default_ram')}: {s['default_min_ram_mb']}–{s['default_max_ram_mb']} MB", "ram"), MenuItem(f"{tr('repair')}: {yn(s['repair_before_launch'])}", "repair"), MenuItem(f"{tr('close_after')}: {yn(s['close_launcher_on_game_start'])}", "close"),
                MenuItem(section_label(tr("settings_updates")), selectable=False),
                MenuItem(f"{tr('auto_update')}: {yn(s['auto_update'])}", "auto_update"), MenuItem(tr("check_updates_now"), "check_update"),
                MenuItem(section_label(tr("settings_actions")), selectable=False),
                MenuItem(tr("open_data"), "open_data"), MenuItem(tr("reset_defaults"), "reset"), MenuItem(tr("back"), "back"),
            ]
            action = self.menu.choose(tr("settings"), items, spacer_after_title=True, view_key="launcher-settings")
            if not action or action == "back": return
            if action == "mc_dir":
                new = self.term.prompt(tr("minecraft_dir"), s["minecraft_dir"]); path = Path(new).expanduser()
                try:
                    path.mkdir(parents=True, exist_ok=True); s["minecraft_dir"] = str(path); self.store.save(); self.data.refresh_installed(self.mll)
                except OSError as exc: self.message("Path unavailable", str(exc), error=True)
            elif action in ("snapshots", "old_versions", "news", "mouse", "repair", "close", "auto_update"):
                key = {"snapshots":"show_snapshots","old_versions":"show_old_versions","news":"news_enabled","mouse":"mouse_enabled","repair":"repair_before_launch","close":"close_launcher_on_game_start","auto_update":"auto_update"}[action]
                s[key] = not s[key]; self.store.save()
            elif action == "check_update":
                self.manual_update_check()
            elif action == "ram":
                s["default_min_ram_mb"] = safe_int(self.term.prompt(tr("ram_min") + " · MB", str(s["default_min_ram_mb"])), 1024, 256, 65536)
                s["default_max_ram_mb"] = safe_int(self.term.prompt(tr("ram_max") + " · MB", str(s["default_max_ram_mb"])), recommended_ram_mb(), 512, 131072)
                if s["default_max_ram_mb"] < s["default_min_ram_mb"]: s["default_max_ram_mb"] = s["default_min_ram_mb"]
                self.store.save()
            elif action == "java":
                new = self.term.prompt(tr("java_manual") + " (use - for auto)", s["custom_java_path"]); new = "" if new == "-" else new
                if new and not Path(new).expanduser().exists(): self.message("Java not found", "Choose an existing java/javaw executable or '-' for automatic Mojang runtime.", error=True)
                else: s["custom_java_path"] = str(Path(new).expanduser()) if new else ""; self.store.save()
            elif action == "language":
                current = s.get("language", "en")
                language_items = [MenuItem(("✓ " if code == current else "  ") + info["name"], code) for code, info in LANGUAGES.items()] + [MenuItem(tr("back"), None)]
                selected_language = self.menu.choose(tr("language_title"), language_items, spacer_after_title=True, view_key="language")
                if selected_language in LANGUAGES and selected_language != current:
                                                                                   
                                                                                        
                    s["language"] = selected_language; self.store.save(); apply_runtime_preferences(s)
            elif action in ("color_primary", "color_subtitle", "color_status", "color_news_filter"):
                key = {"color_primary":"theme_primary","color_subtitle":"theme_subtitle","color_status":"theme_status","color_news_filter":"news_filter_color"}[action]
                normalized = normalize_user_color(self.term.prompt(tr("color_prompt"), s[key]))
                if normalized:
                    s[key] = normalized; self.store.save(); apply_runtime_preferences(s); self.term._last_frame = []; self.term._last_graphic_key = None; self.term._sixel_cache.clear()
                else: self.message("Color", tr("color_error"), error=True)
            elif action == "open_data":
                try:
                    self.store.base.mkdir(parents=True, exist_ok=True)
                    if os.name == "nt": os.startfile(str(self.store.base))                              
                    elif sys.platform == "darwin": subprocess.Popen(["open", str(self.store.base)])
                    else: subprocess.Popen(["xdg-open", str(self.store.base)])
                except Exception as exc: self.message("Open folder", str(exc), error=True)
            elif action == "reset":
                if self.confirm(tr("reset_defaults"), tr("reset_confirm")):
                    self.store.reset_launcher_settings(); apply_runtime_preferences(self.store.settings); self.term._last_frame = []; self.term._last_graphic_key = None; self.term._sixel_cache.clear()
                                                                                  
                                                                              


    def _build_launch_options(self, account: dict[str, Any], version_id: str, auto_java_path: str = "") -> dict[str, Any]:
        cfg = self.store.version_config(version_id)
        if cfg["separate_game_dir"]:
            game_dir = self.store.base / "instances" / slug(version_id)
            game_dir.mkdir(parents=True, exist_ok=True)
        else:
            game_dir = self.store.minecraft_dir
        jvm_args = [f"-Xms{cfg['min_ram_mb']}M", f"-Xmx{cfg['max_ram_mb']}M", "-Dfile.encoding=UTF-8"]
        jvm_args.extend(proxy_jvm_arguments(self.store.proxy_profile()))
        options: dict[str, Any] = {
            "username": account["name"],
            "uuid": account["uuid"],
            "token": "0",
            "launcherName": APP_NAME,
            "launcherVersion": APP_VERSION,
            "jvmArguments": jvm_args,
            "gameDirectory": str(game_dir),
            "customResolution": bool(cfg["custom_resolution"]),
            "resolutionWidth": str(cfg["resolution_width"]),
            "resolutionHeight": str(cfg["resolution_height"]),
        }
        java = self.store.settings.get("custom_java_path") or auto_java_path
        if java:
            options["executablePath"] = java
            options["defaultExecutablePath"] = java
        return options

    def _ensure_legacy_java(self, version_id: str) -> str:
        """Provide Mojang's legacy Java for versions whose metadata has no runtime."""
        if self.store.settings.get("custom_java_path"):
            return ""
        try:
            info = self.mll.runtime.get_version_runtime_information(version_id, str(self.store.minecraft_dir))
            if info is not None:
                return ""                                                           
        except Exception:
                                                                                                 
            return ""
        try:
            runtime_name = "jre-legacy"
            path = self.mll.runtime.get_executable_path(runtime_name, str(self.store.minecraft_dir))
            if path:
                return str(path)
            available = list(self.mll.runtime.get_jvm_runtimes())
            if runtime_name not in available:
                return ""
            self.progress_task(
                tr("install_java_runtime"),
                lambda cb: self.mll.runtime.install_jvm_runtime(runtime_name, str(self.store.minecraft_dir), callback=cb),
            )
            path = self.mll.runtime.get_executable_path(runtime_name, str(self.store.minecraft_dir))
            return str(path or "")
        except Exception as exc:
            self.log.warning("Legacy Java auto-install unavailable: %s", exc)
            return ""

    def play(self) -> None:
        account = self.store.account()
        version_id = self.store.data.get("selected_version")
        if not account:
            self.message(tr("no_account_title"), tr("no_account_body"), error=True)
            return
        if not version_id:
            self.message(tr("no_version_title"), tr("no_version_body"), error=True)
            return
        version_json = self.store.minecraft_dir / "versions" / version_id / f"{version_id}.json"
        if not version_json.exists():
            self.store.data["selected_version"] = None
            self.store.save()
            self.message(tr("version_missing_title"), tr("version_missing_body", version=version_id), error=True)
            return

        try:
            if self.store.settings["repair_before_launch"]:
                self.progress_task(
                    tr("verify_version", version=version_id),
                    lambda cb: self.mll.install.install_minecraft_version(version_id, str(self.store.minecraft_dir), callback=cb),
                )
            auto_java = self._ensure_legacy_java(version_id)
            options = self._build_launch_options(account, version_id, auto_java)
            command = self.mll.command.get_minecraft_command(version_id, str(self.store.minecraft_dir), options)
            if not command:
                raise RuntimeError(tr("empty_launch_command"))
                                                                                   
            command.extend(proxy_game_arguments(self.store.proxy_profile()))
            log_path = self.store.base / "game-latest.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            game_cwd = Path(options["gameDirectory"])
            game_cwd.mkdir(parents=True, exist_ok=True)
            log_file = log_path.open("w", encoding="utf-8", errors="replace")
            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            process = subprocess.Popen(
                command,
                cwd=str(game_cwd),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
            if self.store.settings["close_launcher_on_game_start"]:
                log_file.close()
                self.running = False
                return
            active_proxy = self.store.proxy_profile()
            proxy_line = f"{tr('proxy_label')}: {active_proxy['name']} · {proxy_label(active_proxy)}" if active_proxy else f"{tr('proxy_label')}: {tr('none')}"
            self.term._view_key = f"game-running:{version_id}"
            first_game_frame = True
            while process.poll() is None:
                size = self.term.size()
                w, h = max(20, int(size.columns)), max(8, int(size.lines))
                content_w = max(16, min(140, w - 4))
                content: list[str] = brand_logo_lines(w, h) + [""]
                content.extend(f"{BOLD}{line}{RESET}" for line in wrap_plain(tr("minecraft_running", version=version_id), content_w, max_lines=2))
                for text in (f"{tr('player_label')}: {account['name']}", proxy_line, f"PID {process.pid} · {tr('log_label')}: {log_path}"):
                    content.extend(wrap_plain(text, content_w, max_lines=2))
                if h >= 14:
                    content.extend(["", tr("launcher_returns")])
                frame = [""] * h
                start = max(0, (h - len(content)) // 2)
                for i, line in enumerate(content):
                    if start + i < h:
                        frame[start + i] = center_ansi(line, w)
                self.term.render(frame, animate=first_game_frame)
                first_game_frame = False
                time.sleep(0.10)
            code = int(process.returncode or 0)
            log_file.close()
            if code != 0:
                tail = tail_text(log_path, 16)
                self.message(tr("game_failed_title"), f"{tr('exit_code')}: {code}\n\n{tr('last_log_lines')}:\n{tail or tr('empty_log')}", error=True)
        except Exception as exc:
            self.log.exception("Launch failed")
            self.message(tr("launch_error_title"), f"{type(exc).__name__}: {exc}\n\n{tr('see_launcher_log')}", error=True)
