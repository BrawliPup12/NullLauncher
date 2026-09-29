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

from .config import tr

from .config import APP_NAME, APP_VERSION, LANGUAGES
from .utils import clean_markup

                                                                             

MINECRAFT_NEWS_SOURCE = "https://www.minecraft.net/en-us/about-minecraft"
NEWS_LIMIT = 24

def minecraft_locale(language: str) -> str:
    info = LANGUAGES.get(language) or LANGUAGES["en"]
    return info.get("minecraft_locale", "en-us")

def minecraft_news_sources(locale: str) -> tuple[str, ...]:
    locale = locale if re.fullmatch(r"[a-z]{2}(?:-[a-z]+)?", locale, re.I) else "en-us"
    base = f"https://www.minecraft.net/{locale}"
    return (f"{base}/about-minecraft", f"{base}/article", base)

def localize_minecraft_article_url(url: str, locale: str) -> str:
    if not url or "minecraft.net/" not in url.lower() or "/article/" not in url.lower():
        return url
    return re.sub(r"https://www\.minecraft\.net/[a-z]{2}(?:-[a-z]+)?/article/", f"https://www.minecraft.net/{locale}/article/", url, count=1, flags=re.I)
MINECRAFT_NEWS_ARCHIVE_FEED = "https://mcbe.news/news/official/rss.xml"


class _LatestNewsLinksParser(HTMLParser):
    """Extract the article links from Minecraft.net's "What's new" section."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.collecting = False
        self._heading_tag = ""
        self._heading_parts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag in {"h1", "h2", "h3", "h4"}:
            self._heading_tag = tag
            self._heading_parts = []
            return
        if self.collecting and tag == "a":
            href = dict(attrs).get("href") or ""
            if "/article/" in href:
                self.links.append(href)

    def handle_data(self, data: str) -> None:
        if self._heading_tag:
            self._heading_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == self._heading_tag:
            heading = clean_markup(" ".join(self._heading_parts)).lower()
            if "what's new in minecraft" in heading or "what’s new in minecraft" in heading:
                self.collecting = True
            elif self.collecting and "frequently asked questions" in heading:
                self.collecting = False
            self._heading_tag = ""
            self._heading_parts = []


class _ArticleMetaParser(HTMLParser):
    """Read stable Open Graph/article metadata from an official article page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self._in_title = False
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            values = {str(k).lower(): (v or "") for k, v in attrs}
            key = (values.get("property") or values.get("name") or "").lower()
            content = values.get("content", "").strip()
            if key and content:
                self.meta.setdefault(key, content)

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    @property
    def document_title(self) -> str:
        return clean_markup(" ".join(self._title_parts))


def _download_bytes(
    url: str,
    timeout: float = 7.0,
    max_bytes: int = 6 * 1024 * 1024,
    accept: str = "text/html,application/xhtml+xml,application/rss+xml,application/xml;q=0.9,*/*;q=0.5",
) -> tuple[bytes, str]:
    req = Request(
        url,
        headers={
            "User-Agent": f"{APP_NAME}/{APP_VERSION} (+terminal launcher; Minecraft news reader)",
            "Accept-Language": "en-US,en;q=0.8",
            "Accept": accept,
        },
    )
    with urlopen(req, timeout=timeout) as response:
        raw = response.read(max_bytes)
        charset = response.headers.get_content_charset() or "utf-8"
    return raw, charset


def _download_text(url: str, timeout: float = 7.0) -> str:
    raw, charset = _download_bytes(url, timeout=timeout, max_bytes=4 * 1024 * 1024)
    return raw.decode(charset, errors="replace")


def _download_article_image_bytes(url: str, timeout: float = 8.0) -> bytes:
    raw, _ = _download_bytes(
        url,
        timeout=timeout,
        max_bytes=12 * 1024 * 1024,
        accept="image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    )
    if not raw:
        raise RuntimeError(tr("empty_article_image"))
    return raw


def _extract_latest_article_urls(document: str, limit: int = 20, *, include_all: bool = False) -> list[str]:
    parser = _LatestNewsLinksParser()
    parser.feed(document)
    candidates = parser.links

                                                                               
                                                                        
    if not candidates:
        lowered = document.lower()
        positions = [
            lowered.find("what's new in minecraft"),
            lowered.find("what’s new in minecraft"),
            lowered.find("what&#39;s new in minecraft"),
        ]
        start = min((p for p in positions if p >= 0), default=0)
        segment = document[start:]
        pattern = r"href\s*=\s*[\"']([^\"']*/article/[^\"'#?]+)[^\"']*[\"']"
        candidates = re.findall(pattern, segment, flags=re.I)

    if include_all:
        generic = re.findall(r"href\s*=\s*[\"']([^\"']*/article/[^\"'#?]+)[^\"']*[\"']", document, flags=re.I)
        candidates = list(candidates) + generic

    out: list[str] = []
    seen: set[str] = set()
    for href in candidates:
        url = urljoin(MINECRAFT_NEWS_SOURCE, html.unescape(href)).split("#", 1)[0].split("?", 1)[0]
        if not url.startswith("https://www.minecraft.net/") or "/article/" not in url:
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= limit:
            break
    return out


def _article_from_url(url: str) -> dict[str, Any]:
    document = _download_text(url)
    parser = _ArticleMetaParser()
    parser.feed(document)
    meta = parser.meta
    title = clean_markup(meta.get("og:title") or meta.get("twitter:title") or parser.document_title)
    description = clean_markup(meta.get("og:description") or meta.get("description") or meta.get("twitter:description"))
    published = clean_markup(meta.get("article:published_time") or meta.get("date") or meta.get("datepublished"))
    if "T" in published:
        published = published.split("T", 1)[0]
    canonical = clean_markup(meta.get("og:url")) or url
    if canonical.startswith("/"):
        canonical = urljoin(url, canonical)
    section = clean_markup(meta.get("article:section")) or "Minecraft.net"
    image_url = clean_markup(
        meta.get("og:image:secure_url") or meta.get("og:image") or meta.get("twitter:image")
    )
    if image_url:
        image_url = urljoin(url, image_url)
    if not title:
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        title = clean_markup(slug.replace("-", " ").title()) or "Minecraft News"
    related = _extract_latest_article_urls(document, limit=40, include_all=True)
    return {
        "title": title,
        "description": description,
        "date": published,
        "category": section,
        "url": canonical,
        "image": image_url,
        "image_origin": "page",
        "source": "minecraft.net",
        "_related": [u for u in related if u != url and u != canonical],
    }


def _translate_text_online(text: str, target_language: str, timeout: float = 7.0) -> str:
    source = clean_markup(text)
    if not source or target_language in ("", "en"):
        return source
    query = urlencode({"client": "gtx", "sl": "auto", "tl": target_language, "dt": "t", "q": source[:4500]})
    req = Request("https://translate.googleapis.com/translate_a/single?" + query, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}", "Accept": "application/json"})
    with urlopen(req, timeout=timeout) as response:
        payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8", errors="replace"))
    pieces: list[str] = []
    if isinstance(payload, list) and payload and isinstance(payload[0], list):
        for chunk in payload[0]:
            if isinstance(chunk, list) and chunk and isinstance(chunk[0], str):
                pieces.append(chunk[0])
    return clean_markup("".join(pieces)) or source


NEWS_TRANSLATION_CACHE_VERSION = "translation-v4"
_NEWS_TRANSLATION_BATCH_CHARS = 2400


def _news_field_value(entry: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = entry.get(key)
        if value:
            return clean_markup(value)
    return ""


def _news_translation_ident(entry: dict[str, Any]) -> str:
    ident = _news_identity(entry)
    if ident:
        return ident
    return "hash:" + hashlib.sha256(repr(entry).encode("utf-8", errors="replace")).hexdigest()


def _news_translation_signature(entry: dict[str, Any]) -> str:
    source_text = "\n".join([
        _news_field_value(entry, "title"),
        _news_field_value(entry, "text", "description", "summary", "excerpt"),
        _news_field_value(entry, "category", "tag", "type"),
    ])
    return hashlib.sha256(
        (NEWS_TRANSLATION_CACHE_VERSION + "\n" + source_text).encode("utf-8", errors="replace")
    ).hexdigest()[:20]


def _parse_translation_markers(translated: str) -> dict[tuple[int, str], str]:
    """Parse batch markers that Google Translate normally leaves untouched."""
    out: dict[tuple[int, str], str] = {}
    pattern = re.compile(
        r"__NL(\d{3})([TDC])__\s*(.*?)(?=\s*__NL\d{3}[TDC]__|$)",
        flags=re.S,
    )
    for match in pattern.finditer(str(translated or "")):
        idx = int(match.group(1))
        code = match.group(2)
        value = clean_markup(match.group(3))
        if value:
            out[(idx, code)] = value
    return out


def _translate_news_entry_network(entry: dict[str, Any], target_language: str) -> dict[str, str]:
    """Fallback translator for one article; normally startup uses larger batches."""
    fields = {
        "T": _news_field_value(entry, "title"),
        "D": _news_field_value(entry, "text", "description", "summary", "excerpt"),
        "C": _news_field_value(entry, "category", "tag", "type"),
    }
    payload = " ".join(f"__NL000{code}__ {value}" for code, value in fields.items() if value)
    parsed: dict[tuple[int, str], str] = {}
    if payload:
        try:
            parsed = _parse_translation_markers(_translate_text_online(payload, target_language, timeout=5.5))
        except Exception:
            parsed = {}
    result: dict[str, str] = {}
    mapping = {"T": "title", "D": "description", "C": "category"}
    for code, field in mapping.items():
        source = fields[code]
        if not source:
            continue
        translated = parsed.get((0, code), "")
        if not translated:
            translated = _translate_text_online(source, target_language, timeout=5.5)
        result[field] = clean_markup(translated) or source
    return result


def _translate_news_language_batches(
    entries: list[dict[str, Any]],
    language: str,
    cached_language: dict[str, Any],
) -> dict[str, dict[str, str]]:
    """Translate all stale/missing news for one language using a few large requests."""
    target = LANGUAGES.get(language, LANGUAGES["en"]).get("translate", language)
    if target in ("", "en"):
        return {}

    pending: list[tuple[int, dict[str, Any], str, str]] = []
    for index, entry in enumerate(entries):
        ident = _news_translation_ident(entry)
        signature = _news_translation_signature(entry)
        cached = cached_language.get(ident) if isinstance(cached_language, dict) else None
        if isinstance(cached, dict) and cached.get("signature") == signature:
            continue
        pending.append((index, entry, ident, signature))
    if not pending:
        return {}

                                                                             
                                                                
    batches: list[list[tuple[int, dict[str, Any], str, str]]] = []
    current: list[tuple[int, dict[str, Any], str, str]] = []
    current_len = 0
    for item in pending:
        index, entry, _, _ = item
        record = " ".join([
            f"__NL{index:03d}T__ {_news_field_value(entry, 'title')}",
            f"__NL{index:03d}D__ {_news_field_value(entry, 'text', 'description', 'summary', 'excerpt')}",
            f"__NL{index:03d}C__ {_news_field_value(entry, 'category', 'tag', 'type')}",
        ]).strip()
        record_len = len(record) + 1
        if current and current_len + record_len > _NEWS_TRANSLATION_BATCH_CHARS:
            batches.append(current)
            current = []
            current_len = 0
        current.append(item)
        current_len += record_len
    if current:
        batches.append(current)

    updates: dict[str, dict[str, str]] = {}
    for batch in batches:
        payload_parts: list[str] = []
        expected: dict[int, tuple[dict[str, Any], str, str]] = {}
        for index, entry, ident, signature in batch:
            expected[index] = (entry, ident, signature)
            title = _news_field_value(entry, "title")
            desc = _news_field_value(entry, "text", "description", "summary", "excerpt")
            category = _news_field_value(entry, "category", "tag", "type")
            if title:
                payload_parts.append(f"__NL{index:03d}T__ {title}")
            if desc:
                payload_parts.append(f"__NL{index:03d}D__ {desc}")
            if category:
                payload_parts.append(f"__NL{index:03d}C__ {category}")
        payload = " ".join(payload_parts)
        parsed: dict[tuple[int, str], str] = {}
        request_ok = not bool(payload)
        if payload:
            try:
                translated = _translate_text_online(payload, target, timeout=4.0)
                parsed = _parse_translation_markers(translated)
                request_ok = True
            except Exception:
                                                                              
                                                                              
                break

        for index, (entry, ident, signature) in expected.items():
            source_title = _news_field_value(entry, "title")
            source_desc = _news_field_value(entry, "text", "description", "summary", "excerpt")
            source_category = _news_field_value(entry, "category", "tag", "type")
            row = {
                "signature": signature,
                "title": parsed.get((index, "T"), ""),
                "description": parsed.get((index, "D"), ""),
                "category": parsed.get((index, "C"), ""),
            }
            required_ok = (
                (not source_title or bool(row["title"]))
                and (not source_desc or bool(row["description"]))
                and (not source_category or bool(row["category"]))
            )
            if request_ok and not required_ok:
                                                                            
                                                                       
                try:
                    fallback = _translate_news_entry_network(entry, target)
                    row.update(fallback)
                except Exception:
                    pass
            row["title"] = clean_markup(row.get("title")) or source_title
            row["description"] = clean_markup(row.get("description")) or source_desc
            row["category"] = clean_markup(row.get("category")) or source_category
            updates[ident] = row
    return updates


def pretranslate_news_catalog(
    entries: list[dict[str, Any]],
    existing_cache: Any,
    on_language_done: Optional[Callable[[int, int, str], None]] = None,
) -> dict[str, dict[str, Any]]:
    """Pretranslate news into every supported UI language in parallel.

    Only stale/missing cache entries hit the network.  Each language is handled
    concurrently, while articles inside a language are packed into a handful of
    batch requests.  The returned mapping is merged by the caller on one thread.
    """
    root = existing_cache if isinstance(existing_cache, dict) else {}
    languages = [code for code in LANGUAGES if code != "en"]
    if not entries or not languages:
        return {}
    results: dict[str, dict[str, Any]] = {}
    done = 0
    workers = min(len(languages), 12)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="NullTranslate") as pool:
        futures = {}
        for language in languages:
            cached_language = root.get(language, {}) if isinstance(root.get(language, {}), dict) else {}
            futures[pool.submit(_translate_news_language_batches, entries, language, cached_language)] = language
        for future in as_completed(futures):
            language = futures[future]
            try:
                updates = future.result()
            except Exception:
                updates = {}
            results[language] = updates
            done += 1
            if on_language_done:
                with contextlib.suppress(Exception):
                    on_language_done(done, len(languages), language)
    return results

def _rss_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _feed_date(value: str) -> str:
    value = clean_markup(value)
    if not value:
        return ""
    try:
        dt = parsedate_to_datetime(value)
        return dt.date().isoformat()
    except Exception:
        return value[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", value) else value


def _fetch_archived_official_news(limit: int) -> list[dict[str, Any]]:
    """Supplement Minecraft.net with a public cache of official Minecraft.net posts.

    Minecraft.net's server-rendered news surface currently exposes only a small
    highlighted set. MCBE News publishes a dedicated feed of cached *official*
    Minecraft.net imports; it is used only to discover additional official posts.
    """
    raw, _ = _download_bytes(MINECRAFT_NEWS_ARCHIVE_FEED, timeout=6.0, max_bytes=2 * 1024 * 1024)
    root = ET.fromstring(raw)
    entries: list[dict[str, Any]] = []
    for node in root.iter():
        if _rss_local_name(node.tag) not in ("item", "entry"):
            continue
        fields: dict[str, list[str]] = {}
        attrs: list[dict[str, str]] = []
        for child in node.iter():
            name = _rss_local_name(child.tag)
            text = clean_markup(child.text or "")
            if text:
                fields.setdefault(name, []).append(text)
            if child.attrib:
                attrs.append({str(k).lower(): str(v) for k, v in child.attrib.items()})
        title = (fields.get("title") or [""])[0]
        description = (fields.get("description") or fields.get("summary") or fields.get("content") or [""])[0]
        date = _feed_date((fields.get("pubdate") or fields.get("published") or fields.get("updated") or [""])[0])
        category = (fields.get("category") or ["Minecraft.net"])[0]
        candidate_urls: list[str] = []
        for key in ("link", "guid", "id", "source"):
            candidate_urls.extend(fields.get(key, []))
        blob = " ".join(candidate_urls + [description])
        candidate_urls.extend(re.findall(r"https?://(?:www\.)?minecraft\.net/[^\s<>\"']+", blob, flags=re.I))
        for attr in attrs:
            for key in ("href", "url"):
                if attr.get(key):
                    candidate_urls.append(attr[key])
        official = next((u.rstrip(".,);]") for u in candidate_urls if "minecraft.net/" in u and "/article/" in u), "")
        archive_link = next((u for u in candidate_urls if u.startswith("https://mcbe.news/")), "")
        image = next((a.get("url", "") for a in attrs if a.get("url", "").lower().startswith("http") and re.search(r"image|thumbnail|media", " ".join(a.keys()), re.I)), "")
        if not title:
            continue
        entries.append({
            "title": title,
            "description": description,
            "date": date,
            "category": category or "Minecraft.net",
            "url": official or archive_link,
            "image": image,
            "image_origin": "archive",
            "source": "minecraft.net-archive",
        })
        if len(entries) >= limit:
            break
    return entries


def _news_identity(entry: dict[str, Any]) -> str:
    url = str(entry.get("url") or "").split("?", 1)[0].rstrip("/").lower()
    if url and "/article/" in url:
        return "url:" + url
    title = clean_markup(entry.get("title", "")).lower()
    return "title:" + re.sub(r"[^a-z0-9а-яё]+", " ", title).strip()


def _news_sort_key(entry: dict[str, Any]) -> tuple[str, str]:
    date = str(entry.get("date") or "")
                                                                                   
    iso = date[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", date) else "0000-00-00"
    return (iso, clean_markup(entry.get("title", "")).lower())


def fetch_official_minecraft_news(limit: int = NEWS_LIMIT, locale: str = "en-us") -> list[dict[str, Any]]:
    """Fetch a larger current official-news set without requiring a browser engine."""
    target = max(1, int(limit))
    links: list[str] = []
    seen_links: set[str] = set()
    last_error: Optional[Exception] = None

                                                  
    for source in minecraft_news_sources(locale):
        try:
            document = _download_text(source)
        except Exception as exc:
            last_error = exc
            continue
        for url in _extract_latest_article_urls(document, limit=max(target * 2, 30), include_all=(source != minecraft_news_sources(locale)[0])):
            if url not in seen_links:
                seen_links.add(url)
                links.append(url)
            if len(links) >= target:
                break
        if len(links) >= target:
            break

    results: list[dict[str, Any]] = []
    related_queue: list[str] = []
    if links:
        with ThreadPoolExecutor(max_workers=min(12, len(links)), thread_name_prefix="NullNews") as pool:
            futures = {pool.submit(_article_from_url, url): url for url in links}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    article = future.result()
                except Exception:
                    slug = url.rstrip("/").rsplit("/", 1)[-1]
                    article = {
                        "title": clean_markup(slug.replace("-", " ").title()) or "Minecraft News",
                        "description": "", "date": "", "category": "Minecraft.net",
                        "url": url, "image": "", "source": "minecraft.net", "_related": [],
                    }
                related_queue.extend(article.pop("_related", []) or [])
                results.append(article)

                                                                                
    extra_links: list[str] = []
    for url in related_queue:
        if url not in seen_links:
            seen_links.add(url)
            extra_links.append(url)
        if len(results) + len(extra_links) >= target:
            break
    if extra_links:
        with ThreadPoolExecutor(max_workers=min(12, len(extra_links)), thread_name_prefix="NullNewsMore") as pool:
            futures = {pool.submit(_article_from_url, url): url for url in extra_links}
            for future in as_completed(futures):
                try:
                    article = future.result()
                    article.pop("_related", None)
                    results.append(article)
                except Exception:
                    pass

                                                                              
                                                                                
                                                                                  
    if len(results) < target:
        try:
            results.extend(_fetch_archived_official_news(max(target * 2, 32)))
        except Exception as exc:
            last_error = last_error or exc

    deduped: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for article in sorted(results, key=_news_sort_key, reverse=True):
        ident = _news_identity(article)
        if not ident or ident in seen_ids:
            continue
        seen_ids.add(ident)
        article["_locale"] = locale
        deduped.append(article)
        if len(deduped) >= target:
            break

    if not deduped:
        raise RuntimeError(tr("news_refresh_failed")) from last_error
    return deduped

def _cached_news_is_fresh(entries: Any, max_age_days: int = 180) -> bool:
    if not isinstance(entries, list) or not entries:
        return False
    try:
        from datetime import datetime, timezone, timedelta
        newest = None
        for entry in entries[:20]:
            if not isinstance(entry, dict):
                continue
            value = str(entry.get("date") or entry.get("publishedAt") or entry.get("publishDate") or "").strip()
            if not value:
                continue
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                try:
                    parsed = datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            newest = parsed if newest is None or parsed > newest else newest
        return newest is not None and newest >= datetime.now(timezone.utc) - timedelta(days=max_age_days)
    except Exception:
        return False
