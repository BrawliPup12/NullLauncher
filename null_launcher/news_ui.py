from __future__ import annotations

import os
import webbrowser
from urllib.parse import urljoin
from typing import Any, Optional

from . import config as cfg
from .config import tr
from .news import NEWS_LIMIT, _article_from_url, _download_article_image_bytes, _news_translation_ident, _news_translation_signature
from .terminal import MenuGraphic, MenuItem
from .utils import brand_logo_lines, clean_markup, decode_article_image


class NewsScreenMixin:
    @staticmethod
    def _news_field(entry: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = entry.get(key)
            if value:
                return clean_markup(value)
        return ""

    def _localized_news_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Return the cached translation for the currently selected UI language.

        Only that language is warmed during startup. If its translation failed,
        canonical English text is shown immediately without blocking the view.
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
        return image_url if image_url.startswith("https://") else ""

    def _load_news_image(self, entry: dict[str, Any]) -> Optional[Any]:
        image_url = self._resolve_news_image_url(entry)
        if not image_url:
            return None
        cached = self._news_image_cache.get(image_url)
        if cached is not None:
            return cached
        try:
            raw = self.download_cache.fetch(
                image_url,
                lambda: _download_article_image_bytes(image_url),
                max_age_seconds=30 * 24 * 60 * 60,
            )
            image = decode_article_image(raw)
            self._news_image_cache[image_url] = image
            return image
        except Exception as exc:
            self.log.warning("News image download/decode failed: %s (%s)", image_url, exc)
            self.download_cache.remove(image_url)
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

        title = self._news_field(entry, "title") or tr("news")
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
                    if not webbrowser.open(url): raise RuntimeError(tr("browser_open_failed"))
                except Exception as exc:
                    self.message(tr("browser"), f"{exc}\n\n{url}", error=True)
