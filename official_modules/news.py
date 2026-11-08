from __future__ import annotations

import os
from typing import Any, Optional
from urllib.parse import urljoin
import webbrowser

from null_launcher import config as cfg
from null_launcher.config import APP_VERSION, LANGUAGES, tr
from null_launcher.modules import (
    LauncherModule,
    MenuContribution,
    ModuleContext,
    ModuleMetadata,
    SettingContribution,
)
from null_launcher.news import (
    NEWS_LIMIT,
    _article_from_url,
    _cached_news_is_fresh,
    _download_article_image_bytes,
    _news_translation_ident,
    _news_translation_signature,
    fetch_official_minecraft_news,
    pretranslate_news_catalog,
)
from null_launcher.terminal import MenuGraphic, MenuItem
from null_launcher.utils import brand_logo_lines, clean_markup, decode_article_image, now_iso


class NewsOfficialModule(LauncherModule):
    meta = ModuleMetadata(
        id="news",
        name_key="news",
        description_key="module_news_desc",
        version=APP_VERSION,
        author="NullLauncher",
        order=90,
        can_disable=True,
    )

    def get_menu_items(self, ctx: ModuleContext) -> list[MenuContribution]:
        if ctx.store is not None and not ctx.store.settings.get("news_enabled", True):
            return []
        return [
            MenuContribution(
                id="news",
                label=tr("news"),
                order=self.meta.order,
                location="bottom",
                module_id=self.meta.id,
            )
        ]

    def get_settings_items(self, ctx: ModuleContext) -> list[SettingContribution]:
        if ctx.store is None:
            return []
        yn = tr("yes") if ctx.store.settings.get("news_enabled", True) else tr("no")
        return [
            SettingContribution(
                id="toggle_news_enabled",
                label=f"{tr('load_news')}: {yn}",
                order=10,
                module_id=self.meta.id,
                handler=self._toggle_news_setting,
            ),
            SettingContribution(
                id="color_news_filter",
                label=f"{tr('news_filter_color')}: {ctx.store.settings.get('news_filter_color', '#62F4FF')}",
                order=11,
                module_id=self.meta.id,
                handler=self._change_news_filter_color,
            ),
        ]

    def _toggle_news_setting(self, ctx: ModuleContext) -> None:
        if ctx.store is None:
            return
        ctx.store.settings["news_enabled"] = not bool(ctx.store.settings.get("news_enabled", True))
        ctx.store.save()

    def _change_news_filter_color(self, ctx: ModuleContext) -> None:
        if ctx.store is None:
            return
        from null_launcher.config import apply_runtime_preferences, normalize_user_color

        s = ctx.store.settings
        normalized = normalize_user_color(ctx.prompt(tr("color_prompt"), s["news_filter_color"]))
        if normalized:
            s["news_filter_color"] = normalized
            ctx.store.save()
            apply_runtime_preferences(s)
            if ctx.term is not None:
                ctx.term._last_frame = []
                ctx.term._last_graphic_key = None
                ctx.term._sixel_cache.clear()
        else:
            ctx.message(tr("color_title"), tr("color_error"), error=True)

    def _ensure_news_loaded(self, ctx: ModuleContext) -> None:
        if ctx.data is None or ctx.store is None:
            return
        if ctx.data.news:
            return

        def worker(callbacks: dict[str, Any]) -> None:
            callbacks["setStatus"](tr("preload_news"))
            cached_news = ctx.store.cache.get("news", [])
            news: list[dict[str, Any]] = []
            try:
                news = fetch_official_minecraft_news(limit=NEWS_LIMIT, locale="en-us")
                ctx.store.cache["news"] = news
                ctx.store.cache["news_updated_at"] = now_iso()
            except Exception as exc:
                ctx.log.warning("Official Minecraft news failed: %s", exc)
                if isinstance(cached_news, list) and _cached_news_is_fresh(cached_news):
                    news = list(cached_news)
            ctx.data.news = news
            selected_language = str(ctx.store.settings.get("language") or "en")
            if news and selected_language in LANGUAGES and selected_language != "en":
                callbacks["setStatus"](tr("loading_news"))
                root = ctx.store.cache.get("news_translations", {})
                existing = root if isinstance(root, dict) else {}
                try:
                    updates = pretranslate_news_catalog(news, existing, selected_language)
                    translation_root = ctx.store.cache.setdefault("news_translations", {})
                    if not isinstance(translation_root, dict):
                        translation_root = {}
                        ctx.store.cache["news_translations"] = translation_root
                    for language, language_updates in updates.items():
                        lang_cache = translation_root.setdefault(language, {})
                        if not isinstance(lang_cache, dict):
                            lang_cache = {}
                            translation_root[language] = lang_cache
                        lang_cache.update(language_updates)
                except Exception as exc:
                    ctx.log.warning("News pretranslation failed: %s", exc)
            ctx.store.save_cache()

        ctx.progress_task(tr("loading_news"), worker)

    def open_screen(self, ctx: ModuleContext) -> None:
        self._ensure_news_loaded(ctx)
        if ctx.launcher is not None and hasattr(ctx.launcher, "_default_news_screen"):
            ctx.launcher._default_news_screen()
            return
        self.news_screen(ctx)

    @staticmethod
    def _news_field(entry: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = entry.get(key)
            if value:
                return clean_markup(value)
        return ""

    def _localized_news_entry(self, ctx: ModuleContext, entry: dict[str, Any]) -> dict[str, Any]:
        if ctx.store is None:
            return dict(entry)
        language = str(ctx.store.settings.get("language") or "en")
        if language == "en":
            return dict(entry)
        ident = _news_translation_ident(entry)
        signature = _news_translation_signature(entry)
        root = ctx.store.cache.get("news_translations", {})
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

    def _resolve_news_image_url(self, ctx: ModuleContext, entry: dict[str, Any]) -> str:
        article_url = self._article_url(entry)
        image_url = str(entry.get("image") or "").strip()
        origin = str(entry.get("image_origin") or "").strip().lower()
        if (
            article_url
            and "minecraft.net/" in article_url.lower()
            and "/article/" in article_url.lower()
            and origin != "page"
        ):
            try:
                fresh = _article_from_url(article_url)
                official = str(fresh.get("image") or "").strip()
                if official:
                    image_url = official
                    entry["image"] = official
                    entry["image_origin"] = "page"
            except Exception as exc:
                ctx.log.debug("Article image metadata refresh failed for %s: %s", article_url, exc)
        if image_url and article_url:
            image_url = urljoin(article_url, image_url)
        return image_url if image_url.startswith("https://") else ""

    def _load_news_image(self, ctx: ModuleContext, entry: dict[str, Any]) -> Optional[Any]:
        image_url = self._resolve_news_image_url(ctx, entry)
        if not image_url or ctx.download_cache is None:
            return None
        cache_dict = getattr(ctx.launcher, "_news_image_cache", {}) if ctx.launcher is not None else {}
        cached = cache_dict.get(image_url)
        if cached is not None:
            return cached
        try:
            raw = ctx.download_cache.fetch(
                image_url,
                lambda: _download_article_image_bytes(image_url),
                max_age_seconds=30 * 24 * 60 * 60,
            )
            image = decode_article_image(raw)
            if len(cache_dict) >= 20:
                first_key = next(iter(cache_dict))
                cache_dict.pop(first_key, None)
            cache_dict[image_url] = image
            return image
        except Exception as exc:
            ctx.log.warning("News image download/decode failed: %s (%s)", image_url, exc)
            ctx.download_cache.remove(image_url)
            return None

    def news_screen(self, ctx: ModuleContext) -> None:
        if ctx.data is None or ctx.menu is None:
            return
        if not ctx.data.news:
            ctx.message(tr("news"), tr("news_unavailable"), error=bool(ctx.data.network_errors))
            return
        while True:
            items: list[MenuItem] = []
            for i, entry in enumerate(ctx.data.news[:NEWS_LIMIT]):
                display_entry = self._localized_news_entry(ctx, entry)
                title = self._news_field(display_entry, "title") or f"{tr('article')} {i + 1}"
                category = self._news_field(display_entry, "category", "tag", "type")
                date = self._news_field(entry, "date", "publishDate", "published", "publishedAt")
                items.append(MenuItem(title, ("article", i), hint=" · ".join(x for x in (category, date) if x)))
            items.append(MenuItem(tr("back"), ("back", -1)))
            action = ctx.menu.choose(tr("news"), items, view_key="news-list", spacer_after_title=True)
            if not action or action[0] == "back":
                return
            if action[0] == "article":
                self.news_article_screen(ctx, action[1])

    def news_article_screen(self, ctx: ModuleContext, index: int) -> None:
        if ctx.data is None or ctx.menu is None or ctx.term is None:
            return
        if not (0 <= index < len(ctx.data.news)):
            return
        base_entry = ctx.data.news[index]
        language = str(ctx.store.settings.get("language") or "en") if ctx.store else "en"
        entry = self._localized_news_entry(ctx, base_entry) if language != "en" else dict(base_entry)

        title = self._news_field(entry, "title") or tr("news")
        body = self._news_field(entry, "text", "description", "summary", "excerpt")
        category = self._news_field(entry, "category", "tag", "type")
        date = self._news_field(entry, "date", "publishDate", "published", "publishedAt")
        url = self._article_url(base_entry) or self._article_url(entry)
        real_image = None
        img_url = self._resolve_news_image_url(ctx, entry)
        cache_dict = getattr(ctx.launcher, "_news_image_cache", {}) if ctx.launcher is not None else {}
        if img_url:
            if img_url in cache_dict:
                real_image = cache_dict[img_url]
            else:
                try:
                    real_image = ctx.progress_task(
                        tr("loading_news"),
                        lambda _cb: self._load_news_image(ctx, entry),
                    )
                except Exception:
                    real_image = None
        meta = " · ".join(x for x in (category, date) if x)
        items = ([MenuItem(tr("open_browser"), "open")] if url else []) + [MenuItem(tr("back_news"), "back")]

        def article_graphic(size: os.terminal_size) -> Optional[MenuGraphic]:
            if real_image is None or not ctx.term.supports_sixel():
                return None
            columns, rows = max(20, int(size.columns)), max(8, int(size.lines))
            logo_rows = len(brand_logo_lines(columns, rows))
            max_rows = max(3, rows - logo_rows - 11)
            requested_rows = max(3, min(36, max_rows, int(rows * 0.54)))
            requested_columns = max(20, min(220, columns - 6))
            _, actual_rows = ctx.term.sixel_geometry(real_image, requested_columns, requested_rows)
            return MenuGraphic(
                image=real_image,
                key=f"news-image:{index}:{self._news_field(entry, 'image')}:{cfg.CURRENT_LANGUAGE}",
                rows=max(2, actual_rows),
                columns=max(4, requested_columns),
            )

        def article_subtitle(_size: os.terminal_size) -> str:
            lines: list[str] = []
            if real_image is not None and not ctx.term.supports_sixel():
                lines.append(tr("sixel_required"))
            elif real_image is None:
                lines.append(tr("no_cover"))
            if meta:
                lines.append(meta)
            lines.append(body or tr("no_description"))
            return "\n".join(lines)

        while True:
            action = ctx.menu.choose(
                title,
                items,
                subtitle_factory=article_subtitle,
                graphic_factory=article_graphic,
                view_key=f"news-article:{index}:{cfg.CURRENT_LANGUAGE}",
                content_width_limit=220,
                graphic_reserve_rows=5,
            )
            if not action or action == "back":
                return
            if action == "open" and url:
                try:
                    if not webbrowser.open(url):
                        raise RuntimeError(tr("browser_open_failed"))
                except Exception as exc:
                    ctx.message(tr("browser"), f"{exc}\n\n{url}", error=True)
