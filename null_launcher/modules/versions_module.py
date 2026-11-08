from __future__ import annotations

import contextlib
import shutil
from typing import Callable

from ..catalog import VersionEntry
from ..config import section_label, tr
from ..terminal import MenuItem
from ..utils import normalize_version_type, now_iso, safe_int, slug
from .base import LauncherModule, MenuContribution, ModuleContext, ModuleMetadata


class VersionsModule(LauncherModule):
    meta = ModuleMetadata(
        id="versions",
        name_key="versions",
        description_key="module_versions_desc",
        order=20,
        can_disable=True,
        builtin=True,
    )

    def get_menu_items(self, ctx: ModuleContext) -> list[MenuContribution]:
        return [
            MenuContribution(
                id="versions",
                label=tr("versions"),
                order=self.meta.order,
                module_id=self.meta.id,
            )
        ]

    def open_screen(self, ctx: ModuleContext) -> None:
        self.versions_screen(ctx)

    def versions_screen(self, ctx: ModuleContext) -> None:
        filter_kind = "all"
        query = ""
        base_filter_order = ["all", "vanilla", "fabric", "forge", "neoforge", "quilt"]
        while True:
            filter_names = {
                "all": tr("all"),
                "vanilla": "Vanilla",
                "fabric": "Fabric",
                "forge": "Forge",
                "neoforge": "NeoForge",
                "quilt": "Quilt",
            }
            filter_order = list(base_filter_order)
            providers = ctx.registry.collect_version_providers(ctx) if ctx.registry is not None else []
            for provider in providers:
                with contextlib.suppress(Exception):
                    for fid, flabel in provider.filter_tabs(ctx):
                        if fid not in filter_names:
                            filter_names[fid] = str(flabel)
                            filter_order.append(fid)
            if filter_kind not in filter_names:
                filter_kind = "all"

            catalog: list[VersionEntry] = []
            if ctx.data is not None and filter_kind in base_filter_order:
                catalog = list(ctx.data.build_catalog(filter_kind, query))
            for provider in providers:
                with contextlib.suppress(Exception):
                    extra = provider.build_entries(ctx, filter_kind, query)
                    if isinstance(extra, list):
                        catalog.extend(e for e in extra if isinstance(e, VersionEntry))

            installed_entries = [e for e in catalog if e.installed]
            available_entries = [e for e in catalog if not e.installed]
            items: list[MenuItem] = [
                MenuItem(f"{tr('search')}: {query or tr('none')}", ("search", None)),
                MenuItem(f"{tr('filter')}: {filter_names[filter_kind]}", ("filter", None)),
            ]
            if installed_entries:
                items.append(MenuItem(section_label(tr("installed")), selectable=False))
                for entry in installed_entries:
                    selected = "★" if entry.selected else " "
                    items.append(
                        MenuItem(
                            f"✓{selected} {entry.label}",
                            ("version", entry),
                            hint=normalize_version_type(entry.version_type),
                        )
                    )
            if available_entries:
                items.append(MenuItem(section_label(tr("available")), selectable=False))
                for entry in available_entries:
                    items.append(
                        MenuItem(
                            f"   {entry.label}",
                            ("version", entry),
                            hint=normalize_version_type(entry.version_type),
                        )
                    )
            items.append(MenuItem(tr("back"), ("back", None)))
            if ctx.menu is None:
                return
            action = ctx.menu.choose(
                tr("versions"),
                items,
                subtitle=tr("catalog_summary", count=len(catalog)),
                spacer_after_title=True,
                view_key="versions",
            )
            if not action or action[0] == "back":
                return
            if action[0] == "search":
                query = ctx.prompt(tr("search"), query)
            elif action[0] == "filter":
                filter_kind = filter_order[(filter_order.index(filter_kind) + 1) % len(filter_order)]
            elif action[0] == "version":
                self.version_detail(ctx, action[1])
                if ctx.data is not None and ctx.mll is not None:
                    ctx.data.refresh_installed(ctx.mll)

    def version_detail(self, ctx: ModuleContext, entry: VersionEntry) -> None:
        need_refresh = False
        while True:
            if need_refresh and ctx.data is not None:
                catalog = list(ctx.data.build_catalog("all", ""))
                if ctx.registry is not None:
                    for provider in ctx.registry.collect_version_providers(ctx):
                        with contextlib.suppress(Exception):
                            catalog.extend(provider.build_entries(ctx, "all", ""))
                entry = next(
                    (
                        e
                        for e in catalog
                        if e.key == entry.key or (entry.installed_id and e.installed_id == entry.installed_id)
                    ),
                    entry,
                )
                need_refresh = False
            subtitle = f"{tr('source')}: {entry.loader_id or entry.kind} · Minecraft: {entry.mc_version}"
            items: list[MenuItem] = []
            if entry.installed:
                selected_ver = ctx.store.data.get("selected_version") if ctx.store is not None else None
                if selected_ver != entry.installed_id:
                    items.append(MenuItem(tr("select_launch"), "select"))
                is_fav = ctx.store.is_favorite_version(entry.installed_id) if ctx.store is not None else False
                favorite_key = "favorite_remove" if is_fav else "favorite_add"
                items.append(MenuItem(tr(favorite_key), "favorite"))
                items.append(MenuItem(tr("version_settings"), "settings"))
                items.append(MenuItem(tr("delete"), "delete"))
            else:
                items.append(MenuItem(tr("download"), "install"))
            items.append(MenuItem(tr("back"), "back"))
            if ctx.menu is None:
                return
            action = ctx.menu.choose(entry.label, items, subtitle=subtitle)
            if not action or action == "back":
                return
            if action == "select" and ctx.store is not None:
                ctx.store.data["selected_version"] = entry.installed_id
                ctx.store.save()
                return
            if action == "install":
                if self.install_version(ctx, entry):
                    return
            elif action == "favorite" and ctx.store is not None:
                ctx.store.toggle_favorite_version(entry.installed_id)
            elif action == "settings":
                self.version_settings_screen(ctx, entry.installed_id)
            elif action == "delete":
                if self.delete_version(ctx, entry):
                    return

    def install_version(self, ctx: ModuleContext, entry: VersionEntry) -> bool:
        if ctx.store is None:
            return False
        if ctx.registry is not None:
            for provider in ctx.registry.collect_version_providers(ctx):
                if provider.can_handle(ctx, entry):
                    try:
                        installed_id = provider.install(ctx, entry)
                        if not installed_id:
                            return False
                        ctx.store.data["managed_versions"][entry.key] = {
                            "kind": entry.kind,
                            "loader_id": entry.loader_id,
                            "mc_version": entry.mc_version,
                            "installed_id": str(installed_id),
                            "installed_at": now_iso(),
                        }
                        ctx.store.data["selected_version"] = str(installed_id)
                        ctx.store.save()
                        if ctx.data is not None and ctx.mll is not None:
                            ctx.data.refresh_installed(ctx.mll)
                        ctx.message(
                            tr("install_complete_title"),
                            tr("install_complete_body", label=entry.label, installed_id=installed_id),
                        )
                        return True
                    except Exception as exc:
                        ctx.log.exception("Custom provider install failed: %s", entry.key)
                        ctx.message(
                            tr("install_error_title"),
                            f"{type(exc).__name__}: {exc}\n\n{tr('details_in_log')}",
                            error=True,
                        )
                        return False

        if entry.loader_id == "forge":
            if not ctx.confirm(tr("forge_install_title"), tr("forge_install_warning")):
                return False
        mc_dir = str(ctx.store.minecraft_dir)
        try:
            if entry.kind == "vanilla":
                def task(cb: dict[str, Callable]) -> str:
                    ctx.mll.install.install_minecraft_version(entry.mc_version, mc_dir, callback=cb)
                    return entry.mc_version
            elif entry.kind == "loader":
                def task(cb: dict[str, Callable]) -> str:
                    loader = ctx.mll.mod_loader.get_mod_loader(entry.loader_id)
                    java = ctx.store.settings.get("custom_java_path") or None
                    return str(loader.install(entry.mc_version, mc_dir, callback=cb, java=java))
            else:
                ctx.message(tr("cannot_download_title"), tr("local_version_required"), error=True)
                return False
            installed_id = str(ctx.progress_task(tr("installing_version", version=entry.label), task))
            ctx.store.data["managed_versions"][entry.key] = {
                "kind": entry.kind,
                "loader_id": entry.loader_id,
                "mc_version": entry.mc_version,
                "installed_id": installed_id,
                "installed_at": now_iso(),
            }
            ctx.store.data["selected_version"] = installed_id
            ctx.store.save()
            if ctx.data is not None and ctx.mll is not None:
                ctx.data.refresh_installed(ctx.mll)
            ctx.message(
                tr("install_complete_title"),
                tr("install_complete_body", label=entry.label, installed_id=installed_id),
            )
            return True
        except Exception as exc:
            ctx.log.exception("Install failed: %s", entry.key)
            ctx.message(
                tr("install_error_title"),
                f"{type(exc).__name__}: {exc}\n\n{tr('details_in_log')}",
                error=True,
            )
            return False

    def delete_version(self, ctx: ModuleContext, entry: VersionEntry) -> bool:
        if ctx.store is None:
            return False
        vid = entry.installed_id
        if not vid:
            return False
        if not ctx.confirm(tr("delete_version_title"), tr("delete_version_body", version=vid)):
            return False
        if ctx.registry is not None:
            for provider in ctx.registry.collect_version_providers(ctx):
                if provider.can_handle(ctx, entry):
                    custom_res = provider.delete(ctx, entry)
                    if custom_res is not None:
                        return bool(custom_res)
        versions_root = (ctx.store.minecraft_dir / "versions").resolve()
        target = (versions_root / vid).resolve()
        try:
            if target.parent != versions_root:
                raise RuntimeError(tr("unsafe_version_path"))
            if target.exists():
                shutil.rmtree(target)
            instance_dir = (ctx.store.base / "instances" / slug(vid)).resolve()
            if instance_dir.exists() and instance_dir.parent == (ctx.store.base / "instances").resolve():
                with contextlib.suppress(OSError):
                    shutil.rmtree(instance_dir)
            for key, item in list(ctx.store.data["managed_versions"].items()):
                if isinstance(item, dict) and item.get("installed_id") == vid:
                    ctx.store.data["managed_versions"].pop(key, None)
            ctx.store.data["version_settings"].pop(vid, None)
            if ctx.store.data.get("selected_version") == vid:
                ctx.store.data["selected_version"] = None
            ctx.store.data["favorite_versions"] = [item for item in ctx.store.favorite_versions() if item != vid]
            if ctx.store.last_launch().get("version") == vid:
                ctx.store.data["last_launch"] = {}
            ctx.store.save()
            if ctx.data is not None and ctx.mll is not None:
                ctx.data.refresh_installed(ctx.mll)
            return True
        except Exception as exc:
            ctx.log.exception("Delete failed: %s", vid)
            ctx.message(tr("delete_failed_title"), str(exc), error=True)
            return False

    def version_settings_screen(self, ctx: ModuleContext, installed_id: str) -> None:
        if ctx.store is None or ctx.menu is None:
            return
        while True:
            vcfg = ctx.store.version_config(installed_id)
            yn = lambda v: tr("yes") if v else tr("no")
            items = [
                MenuItem(section_label(tr("version_memory")), selectable=False),
                MenuItem(f"{tr('ram_min')}: {vcfg['min_ram_mb']} MB", "min_ram"),
                MenuItem(f"{tr('ram_max')}: {vcfg['max_ram_mb']} MB", "max_ram"),
                MenuItem(section_label(tr("version_window")), selectable=False),
                MenuItem(f"{tr('custom_resolution')}: {yn(vcfg['custom_resolution'])}", "resolution_toggle"),
                MenuItem(f"{tr('window_size')}: {vcfg['resolution_width']}×{vcfg['resolution_height']}", "resolution"),
                MenuItem(section_label(tr("version_files")), selectable=False),
                MenuItem(f"{tr('separate_dir')}: {yn(vcfg['separate_game_dir'])}", "separate"),
                MenuItem(section_label(tr("version_actions")), selectable=False),
                MenuItem(tr("reset_defaults"), "reset"),
                MenuItem(tr("back"), "back"),
            ]
            action = ctx.menu.choose(
                f"{tr('version_settings')} · {installed_id}",
                items,
                spacer_after_title=True,
                view_key=f"version-settings:{installed_id}",
            )
            if not action or action == "back":
                return
            if action in ("min_ram", "max_ram"):
                key = "min_ram_mb" if action == "min_ram" else "max_ram_mb"
                raw = ctx.prompt(tr("ram_min") if action == "min_ram" else tr("ram_max"), str(vcfg[key]))
                vcfg[key] = safe_int(raw, vcfg[key], 256 if key == "min_ram_mb" else 512, 131072)
                if vcfg["max_ram_mb"] < vcfg["min_ram_mb"]:
                    vcfg["max_ram_mb"] = vcfg["min_ram_mb"]
            elif action == "resolution_toggle":
                vcfg["custom_resolution"] = not vcfg["custom_resolution"]
            elif action == "resolution":
                w = ctx.prompt(tr("window_size") + " · " + tr("width_label"), str(vcfg["resolution_width"]))
                h = ctx.prompt(tr("window_size") + " · " + tr("height_label"), str(vcfg["resolution_height"]))
                vcfg["resolution_width"] = safe_int(w, vcfg["resolution_width"], 320, 16384)
                vcfg["resolution_height"] = safe_int(h, vcfg["resolution_height"], 240, 16384)
            elif action == "separate":
                vcfg["separate_game_dir"] = not vcfg["separate_game_dir"]
            elif action == "reset":
                if ctx.confirm(tr("reset_defaults"), tr("version_reset_confirm")):
                    ctx.store.reset_version_settings(installed_id)
                continue
            ctx.store.data["version_settings"][installed_id] = vcfg
            ctx.store.save()
