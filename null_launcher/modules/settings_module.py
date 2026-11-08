from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from .. import config as cfg
from ..config import LANGUAGES, apply_runtime_preferences, normalize_user_color, section_label, tr
from ..terminal import MenuItem
from ..updater import clear_update_download_cache
from ..utils import recommended_ram_mb, safe_int
from .base import LauncherModule, MenuContribution, ModuleContext, ModuleMetadata


class SettingsModule(LauncherModule):
    meta = ModuleMetadata(
        id="settings",
        name_key="settings",
        description_key="module_settings_desc",
        order=60,
        can_disable=False,
        builtin=True,
    )

    def get_menu_items(self, ctx: ModuleContext) -> list[MenuContribution]:
        return [
            MenuContribution(
                id="settings",
                label=tr("settings"),
                order=self.meta.order,
                module_id=self.meta.id,
            )
        ]

    def open_screen(self, ctx: ModuleContext) -> None:
        self.settings_screen(ctx)

    def settings_screen(self, ctx: ModuleContext) -> None:
        if ctx.store is None or ctx.menu is None:
            return
        while True:
            apply_runtime_preferences(ctx.store.settings)
            s = ctx.store.settings
            yn = lambda v: tr("yes") if v else tr("no")
            lang_name = LANGUAGES.get(s.get("language", "en"), LANGUAGES["en"])["name"]
            items = [
                MenuItem(section_label(tr("settings_paths")), selectable=False),
                MenuItem(f"{tr('minecraft_dir')}: {s['minecraft_dir']}", "mc_dir"),
                MenuItem(f"{tr('java_manual')}: {s['custom_java_path'] or tr('auto_runtime')}", "java"),
                MenuItem(section_label(tr("settings_catalog")), selectable=False),
                MenuItem(f"{tr('show_snapshots')}: {yn(s['show_snapshots'])}", "snapshots"),
                MenuItem(f"{tr('show_old')}: {yn(s['show_old_versions'])}", "old_versions"),
                MenuItem(section_label(tr("settings_interface")), selectable=False),
                MenuItem(f"{tr('language')}: {lang_name}", "language"),
                MenuItem(f"{tr('primary_color')}: {s['theme_primary']}", "color_primary"),
                MenuItem(f"{tr('subtitle_color')}: {s['theme_subtitle']}", "color_subtitle"),
                MenuItem(f"{tr('status_color')}: {s['theme_status']}", "color_status"),
                MenuItem(f"{tr('mouse_menu')}: {yn(s['mouse_enabled'])}", "mouse", hint=tr("after_restart")),
                MenuItem(section_label(tr("settings_launch")), selectable=False),
                MenuItem(f"{tr('default_ram')}: {s['default_min_ram_mb']}–{s['default_max_ram_mb']} MB", "ram"),
                MenuItem(f"{tr('repair')}: {yn(s['repair_before_launch'])}", "repair"),
                MenuItem(f"{tr('close_after')}: {yn(s['close_launcher_on_game_start'])}", "close"),
                MenuItem(section_label(tr("settings_updates")), selectable=False),
                MenuItem(f"{tr('auto_update')}: {yn(s['auto_update'])}", "auto_update"),
                MenuItem(tr("check_updates_now"), "check_update"),
            ]

            custom_settings = ctx.registry.collect_settings_items(ctx) if ctx.registry is not None else []
            if custom_settings:
                items.append(MenuItem(section_label(tr("settings_modules_custom")), selectable=False))
                for contrib in custom_settings:
                    items.append(MenuItem(contrib.label, ("module_setting", contrib.id), hint=contrib.hint))

            items.extend(
                [
                    MenuItem(section_label(tr("settings_actions")), selectable=False),
                    MenuItem(tr("modules"), "open_modules"),
                    MenuItem(tr("clear_download_cache"), "clear_cache"),
                    MenuItem(tr("open_data"), "open_data"),
                    MenuItem(tr("reset_defaults"), "reset"),
                    MenuItem(tr("back"), "back"),
                ]
            )
            action = ctx.menu.choose(tr("settings"), items, spacer_after_title=True, view_key="launcher-settings")
            if not action or action == "back":
                return
            if isinstance(action, tuple) and len(action) == 2 and action[0] == "module_setting":
                if ctx.registry is not None:
                    ctx.registry.dispatch_settings_action(ctx, action[1])
                continue
            if action == "open_modules":
                if ctx.registry is not None:
                    mod = ctx.registry.get_active_module("modules")
                    if mod is not None:
                        mod.open_screen(ctx)
                continue
            if action == "mc_dir":
                new = ctx.prompt(tr("minecraft_dir"), s["minecraft_dir"]).strip()
                if new:
                    path = Path(new).expanduser()
                    if path.is_file():
                        ctx.message(tr("path_unavailable_title"), tr("path_unavailable_title"), error=True)
                    else:
                        try:
                            path.mkdir(parents=True, exist_ok=True)
                            s["minecraft_dir"] = str(path.resolve())
                            ctx.store.save()
                            if ctx.data is not None and ctx.mll is not None:
                                ctx.data.refresh_installed(ctx.mll)
                        except OSError as exc:
                            ctx.message(tr("path_unavailable_title"), str(exc), error=True)
            elif action in ("snapshots", "old_versions", "news", "mouse", "repair", "close", "auto_update"):
                key = {
                    "snapshots": "show_snapshots",
                    "old_versions": "show_old_versions",
                    "news": "news_enabled",
                    "mouse": "mouse_enabled",
                    "repair": "repair_before_launch",
                    "close": "close_launcher_on_game_start",
                    "auto_update": "auto_update",
                }[action]
                s[key] = not s[key]
                ctx.store.save()
            elif action == "check_update":
                if ctx.launcher is not None and hasattr(ctx.launcher, "manual_update_check"):
                    ctx.launcher.manual_update_check()
            elif action == "ram":
                s["default_min_ram_mb"] = safe_int(
                    ctx.prompt(tr("ram_min") + " · MB", str(s["default_min_ram_mb"])), 1024, 256, 65536
                )
                s["default_max_ram_mb"] = safe_int(
                    ctx.prompt(tr("ram_max") + " · MB", str(s["default_max_ram_mb"])),
                    recommended_ram_mb(),
                    512,
                    131072,
                )
                if s["default_max_ram_mb"] < s["default_min_ram_mb"]:
                    s["default_max_ram_mb"] = s["default_min_ram_mb"]
                ctx.store.save()
            elif action == "java":
                new = ctx.prompt(
                    tr("java_manual") + " (" + tr("use_dash_for_auto") + ")", s["custom_java_path"]
                ).strip()
                new = "" if new == "-" else new
                if new:
                    java_path = Path(new).expanduser()
                    if not java_path.is_file():
                        ctx.message(tr("java_not_found_title"), tr("java_not_found_body"), error=True)
                    else:
                        s["custom_java_path"] = str(java_path.resolve())
                        ctx.store.save()
                else:
                    s["custom_java_path"] = ""
                    ctx.store.save()
            elif action == "language":
                current = s.get("language", "en")
                language_items = [
                    MenuItem(("✓ " if code == current else "  ") + info["name"], code)
                    for code, info in LANGUAGES.items()
                ] + [MenuItem(tr("back"), None)]
                selected_language = ctx.menu.choose(
                    tr("language_title"), language_items, spacer_after_title=True, view_key="language"
                )
                if selected_language in LANGUAGES and selected_language != current:
                    s["language"] = selected_language
                    ctx.store.save()
                    apply_runtime_preferences(s)
            elif action in ("color_primary", "color_subtitle", "color_status", "color_news_filter"):
                key = {
                    "color_primary": "theme_primary",
                    "color_subtitle": "theme_subtitle",
                    "color_status": "theme_status",
                    "color_news_filter": "news_filter_color",
                }[action]
                normalized = normalize_user_color(ctx.prompt(tr("color_prompt"), s[key]))
                if normalized:
                    s[key] = normalized
                    ctx.store.save()
                    apply_runtime_preferences(s)
                    if ctx.term is not None:
                        ctx.term._last_frame = []
                        ctx.term._last_graphic_key = None
                        ctx.term._sixel_cache.clear()
                else:
                    ctx.message(tr("color_title"), tr("color_error"), error=True)
            elif action == "clear_cache":
                if ctx.download_cache is not None:
                    ctx.download_cache.clear()
                clear_update_download_cache()
                if ctx.launcher is not None and hasattr(ctx.launcher, "_news_image_cache"):
                    ctx.launcher._news_image_cache.clear()
                if ctx.term is not None:
                    ctx.term._sixel_cache.clear()
                ctx.message(tr("clear_download_cache"), tr("download_cache_cleared"))
            elif action == "open_data":
                try:
                    ctx.store.base.mkdir(parents=True, exist_ok=True)
                    if os.name == "nt":
                        os.startfile(str(ctx.store.base))
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", str(ctx.store.base)])
                    else:
                        subprocess.Popen(["xdg-open", str(ctx.store.base)])
                except Exception as exc:
                    ctx.message(tr("open_folder_title"), str(exc), error=True)
            elif action == "reset":
                if ctx.confirm(tr("reset_defaults"), tr("reset_confirm")):
                    ctx.store.reset_launcher_settings()
                    apply_runtime_preferences(ctx.store.settings)
                    if ctx.term is not None:
                        ctx.term._last_frame = []
                        ctx.term._last_graphic_key = None
                        ctx.term._sixel_cache.clear()
                    if ctx.registry is not None:
                        ctx.registry.resolve_and_activate(ctx)
