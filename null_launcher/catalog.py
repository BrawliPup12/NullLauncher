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

from .config import LANGUAGES, tr
from .news import NEWS_LIMIT, _cached_news_is_fresh, fetch_official_minecraft_news, pretranslate_news_catalog
from .state import StateStore
from .utils import natural_version_key, now_iso

                                                                             

@dataclasses.dataclass
class VersionEntry:
    key: str
    label: str
    kind: str                            
    mc_version: str
    version_type: str = "release"
    release_time: str = ""
    loader_id: str = ""
    installed_id: str = ""
    installed: bool = False
    selected: bool = False


class LauncherData:
    def __init__(self, store: StateStore, logger: logging.Logger):
        self.store = store
        self.log = logger
        self.vanilla: list[dict[str, Any]] = []
        self.installed: list[dict[str, Any]] = []
        self.loader_versions: dict[str, list[str]] = {}
        self.news: list[dict[str, Any]] = []
        self.latest_release: str = ""
        self.network_errors: list[str] = []

    def _cache_get(self, key: str, default: Any) -> Any:
        return self.store.cache.get(key, default)

    def preload(self, mll: Any, progress: Callable[[int, int, str], None]) -> None:
        """Load independent launcher data concurrently, then warm translations.

        Network-heavy catalog/news requests no longer wait for each other.  Once
        the news list arrives, translations for every supported language are
        generated in parallel and cached before the splash screen finishes.
        """
        translation_languages = [code for code in LANGUAGES if code != "en"]
        total = 5 + len(translation_languages)                                               
        step = 0
        progress(step, total, tr("preload_local"))
        self.store.minecraft_dir.mkdir(parents=True, exist_ok=True)

        try:
            self.installed = list(mll.utils.get_installed_versions(str(self.store.minecraft_dir)))
        except Exception as exc:
            self.log.exception("get_installed_versions failed")
            self.installed = []
            self.network_errors.append(f"Локальные версии: {exc}")
        step += 1

        cached_vanilla = self._cache_get("vanilla", [])
        cached_latest = str(self._cache_get("latest_release", ""))
        cached_loaders_raw = self._cache_get("loaders", {})
        cached_loaders = cached_loaders_raw if isinstance(cached_loaders_raw, dict) else {}
        cached_news = self._cache_get("news", [])

        def cache_stamp_fresh(key: str, ttl_seconds: int) -> bool:
            value = str(self.store.cache.get(key) or "").strip()
            if not value:
                return False
            try:
                from datetime import datetime, timezone
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return (datetime.now(timezone.utc) - parsed).total_seconds() <= ttl_seconds
            except Exception:
                return False

        vanilla_cache_fresh = cache_stamp_fresh("vanilla_updated_at", 30 * 60)
        loaders_cache_fresh = cache_stamp_fresh("loaders_updated_at", 30 * 60)
        news_cache_fresh = cache_stamp_fresh("news_updated_at", 10 * 60) and _cached_news_is_fresh(cached_news)

        def load_vanilla() -> tuple[list[dict[str, Any]], str, Optional[str], bool]:
            if vanilla_cache_fresh and isinstance(cached_vanilla, list) and cached_vanilla:
                return list(cached_vanilla), cached_latest, None, False
            try:
                versions = list(mll.utils.get_version_list())
                latest = mll.utils.get_latest_version()
                return versions, str(latest.get("release", "")), None, True
            except Exception as exc:
                self.log.warning("Vanilla list network error: %s", exc)
                message = "Vanilla: офлайн-кэш" if cached_vanilla else "Vanilla: недоступно"
                return list(cached_vanilla) if isinstance(cached_vanilla, list) else [], cached_latest, message, False

        def load_loaders() -> tuple[dict[str, list[str]], list[str], bool]:
            if loaders_cache_fresh and cached_loaders:
                return {str(k): list(v) for k, v in cached_loaders.items() if isinstance(v, list)}, [], False
            loader_ids = ["forge", "neoforge", "fabric", "quilt"]
            results: dict[str, list[str]] = {}
            errors: list[str] = []

            def get_versions(loader_id: str) -> tuple[str, list[str]]:
                loader = mll.mod_loader.get_mod_loader(loader_id)
                return loader_id, list(loader.get_minecraft_versions(True))

            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="NullLoaders") as pool:
                futures = {pool.submit(get_versions, lid): lid for lid in loader_ids}
                for fut in as_completed(futures):
                    lid = futures[fut]
                    try:
                        _, versions = fut.result()
                        results[lid] = versions
                    except Exception as exc:
                        self.log.warning("Loader %s list failed: %s", lid, exc)
                        cached = cached_loaders.get(lid, []) if isinstance(cached_loaders, dict) else []
                        results[lid] = list(cached) if isinstance(cached, list) else []
                        errors.append(f"{lid}: офлайн-кэш" if cached else f"{lid}: недоступно")
            return results, errors, True

        def load_news() -> tuple[list[dict[str, Any]], Optional[str], bool]:
            if not self.store.settings["news_enabled"]:
                return [], None, False
            if news_cache_fresh and isinstance(cached_news, list) and cached_news:
                return list(cached_news), None, False
            try:
                news = fetch_official_minecraft_news(limit=NEWS_LIMIT, locale="en-us")
                return news, None, True
            except Exception as exc:
                self.log.warning("Official Minecraft news failed: %s", exc)
                fallback = cached_news if _cached_news_is_fresh(cached_news) else []
                return list(fallback) if isinstance(fallback, list) else [], (
                    "Новости: свежий офлайн-кэш" if fallback else "Новости: недоступны"
                ), False

        combined_status = " · ".join((tr("preload_vanilla"), tr("preload_loaders"), tr("preload_news")))
        progress(step, total, combined_status)
        task_labels = {
            "vanilla": tr("preload_vanilla"),
            "loaders": tr("preload_loaders"),
            "news": tr("preload_news"),
        }
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="NullStartup") as pool:
            futures = {
                pool.submit(load_vanilla): "vanilla",
                pool.submit(load_loaders): "loaders",
                pool.submit(load_news): "news",
            }
            for future in as_completed(futures):
                kind = futures[future]
                if kind == "vanilla":
                    versions, latest_release, error, fetched = future.result()
                    self.vanilla = versions
                    self.latest_release = latest_release
                    self.store.cache["vanilla"] = versions
                    self.store.cache["latest_release"] = latest_release
                    if fetched:
                        self.store.cache["vanilla_updated_at"] = now_iso()
                    if error:
                        self.network_errors.append(error)
                elif kind == "loaders":
                    versions_by_loader, errors, fetched = future.result()
                    self.loader_versions = versions_by_loader
                    self.store.cache["loaders"] = versions_by_loader
                    if fetched:
                        self.store.cache["loaders_updated_at"] = now_iso()
                    self.network_errors.extend(errors)
                else:
                    news, error, fetched = future.result()
                    self.news = news
                    if news:
                        self.store.cache["news"] = news
                    if fetched:
                        self.store.cache["news_updated_at"] = now_iso()
                    if error:
                        self.network_errors.append(error)
                step += 1
                progress(step, total, task_labels[kind])

                                                                                  
                                                                                   
        if self.news and translation_languages:
            root = self.store.cache.get("news_translations", {})
            existing = root if isinstance(root, dict) else {}

            def translation_progress(done: int, count: int, language: str) -> None:
                nonlocal step
                                                                             
                                                                      
                step = 4 + min(done, len(translation_languages))
                language_name = LANGUAGES.get(language, {}).get("name", language)
                progress(step, total, f"{tr('loading_news')} · {language_name} · {done}/{count}")

            try:
                updates = pretranslate_news_catalog(self.news, existing, translation_progress)
                translation_root = self.store.cache.setdefault("news_translations", {})
                if not isinstance(translation_root, dict):
                    translation_root = {}
                    self.store.cache["news_translations"] = translation_root
                for language, language_updates in updates.items():
                    lang_cache = translation_root.setdefault(language, {})
                    if not isinstance(lang_cache, dict):
                        lang_cache = {}
                        translation_root[language] = lang_cache
                    lang_cache.update(language_updates)
            except Exception as exc:
                                                                                 
                                                                                  
                self.log.warning("News pretranslation failed: %s", exc)
            step = 4 + len(translation_languages)
        else:
            step = 4 + len(translation_languages)

        progress(step, total, tr("preload_profiles"))
        self._clean_managed_versions()
        self.store.cache["updated_at"] = now_iso()
        with contextlib.suppress(Exception):
            self.store.save_cache()
        step += 1
        progress(total, total, tr("ready"))

    def _clean_managed_versions(self) -> None:
        changed = False
        for key, item in list(self.store.data["managed_versions"].items()):
            if not isinstance(item, dict):
                self.store.data["managed_versions"].pop(key, None)
                changed = True
                continue
            installed_id = str(item.get("installed_id", ""))
            if not installed_id or not (self.store.minecraft_dir / "versions" / installed_id / f"{installed_id}.json").exists():
                self.store.data["managed_versions"].pop(key, None)
                changed = True
        if changed:
            self.store.save()

    def refresh_installed(self, mll: Any) -> None:
        try:
            self.installed = list(mll.utils.get_installed_versions(str(self.store.minecraft_dir)))
        except Exception:
            self.log.exception("refresh_installed failed")

    def headline(self) -> str:
        if not self.news:
            return ""
        title = str(self.news[0].get("title", "")).strip()
        return html.unescape(re.sub(r"<[^>]+>", "", title))

    def build_catalog(self, filter_kind: str = "all", query: str = "") -> list[VersionEntry]:
        settings = self.store.settings
        managed = self.store.data["managed_versions"]
        selected_id = self.store.data.get("selected_version")
        installed_ids = {str(v.get("id", "")) for v in self.installed if isinstance(v, dict)}
        out: list[VersionEntry] = []
        represented_installed: set[str] = set()
        release_times = {
            str(v.get("id", "")): str(v.get("releaseTime") or v.get("time") or "")
            for v in self.vanilla if isinstance(v, dict) and v.get("id")
        }

        def add(entry: VersionEntry) -> None:
            if filter_kind != "all" and entry.kind == "loader" and entry.loader_id != filter_kind:
                return
            if filter_kind == "vanilla" and entry.kind != "vanilla":
                return
            if filter_kind not in ("all", "vanilla") and entry.kind != "loader":
                return
            if query and query.lower() not in (entry.label + " " + entry.installed_id).lower():
                return
            out.append(entry)

        for v in self.vanilla:
            if not isinstance(v, dict):
                continue
            vid = str(v.get("id", ""))
            vtype = str(v.get("type", ""))
            if not vid:
                continue
            if vtype == "snapshot" and not settings["show_snapshots"]:
                continue
            if vtype in ("old_alpha", "old_beta") and not settings["show_old_versions"]:
                continue
            key = "vanilla:" + vid
            item = managed.get(key, {}) if isinstance(managed.get(key), dict) else {}
            installed_id = str(item.get("installed_id") or (vid if vid in installed_ids else ""))
            installed = bool(installed_id and installed_id in installed_ids)
            if installed:
                represented_installed.add(installed_id)
            add(VersionEntry(
                key=key,
                label=f"Vanilla {vid}",
                kind="vanilla",
                mc_version=vid,
                version_type=vtype,
                release_time=str(v.get("releaseTime") or v.get("time") or ""),
                installed_id=installed_id,
                installed=installed,
                selected=bool(installed_id and installed_id == selected_id),
            ))

        loader_names = {"forge": "Forge", "neoforge": "NeoForge", "fabric": "Fabric", "quilt": "Quilt"}
        for lid in ("forge", "neoforge", "fabric", "quilt"):
            if filter_kind not in ("all", lid):
                continue
            for mc in self.loader_versions.get(lid, []):
                key = f"{lid}:{mc}"
                item = managed.get(key, {}) if isinstance(managed.get(key), dict) else {}
                installed_id = str(item.get("installed_id", ""))
                installed = installed_id in installed_ids if installed_id else False
                if installed:
                    represented_installed.add(installed_id)
                add(VersionEntry(
                    key=key,
                    label=f"{loader_names[lid]} {mc}",
                    kind="loader",
                    loader_id=lid,
                    mc_version=str(mc),
                    release_time=release_times.get(str(mc), ""),
                    installed_id=installed_id,
                    installed=installed,
                    selected=bool(installed_id and installed_id == selected_id),
                ))

                                                                            
        if filter_kind == "all":
            for v in self.installed:
                if not isinstance(v, dict):
                    continue
                vid = str(v.get("id", ""))
                if not vid or vid in represented_installed:
                    continue
                                                                  
                if any(e.kind == "vanilla" and e.mc_version == vid for e in out):
                    continue
                entry = VersionEntry(
                    key="local:" + vid,
                    label=f"Local {vid}",
                    kind="local",
                    mc_version=vid,
                    version_type=str(v.get("type", "local")),
                    release_time=release_times.get(vid, ""),
                    installed_id=vid,
                    installed=True,
                    selected=vid == selected_id,
                )
                if not query or query.lower() in (entry.label + " " + vid).lower():
                    out.append(entry)

                                                                         
        source_order = {"vanilla": 0, "fabric": 1, "forge": 2, "neoforge": 3, "quilt": 4, "local": 5}
        out.sort(key=lambda e: (source_order.get(e.loader_id or e.kind, 9), e.label.lower()))
        out.sort(key=lambda e: (e.release_time or "", natural_version_key(e.mc_version)), reverse=True)
        out.sort(key=lambda e: 0 if e.installed else 1)
        return out
