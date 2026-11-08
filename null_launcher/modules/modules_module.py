from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from ..config import section_label, tr
from ..terminal import MenuItem
from .base import LauncherModule, MenuContribution, ModuleContext, ModuleMetadata
from .registry import ModuleRecord


class ModulesManagerModule(LauncherModule):
    meta = ModuleMetadata(
        id="modules",
        name_key="modules",
        description_key="module_modules_desc",
        order=50,
        can_disable=False,
        builtin=True,
    )

    def get_menu_items(self, ctx: ModuleContext) -> list[MenuContribution]:
        return [
            MenuContribution(
                id="modules",
                label=tr("modules"),
                order=self.meta.order,
                module_id=self.meta.id,
            )
        ]

    def open_screen(self, ctx: ModuleContext) -> None:
        self.modules_screen(ctx)

    def _status_text(self, rec: ModuleRecord) -> tuple[str, str]:
        if rec.error:
            return "!", tr("module_status_error")
        if rec.replaced_by:
            return "↺", tr("module_status_replaced", by=rec.replaced_by)
        if not rec.enabled:
            return "✕", tr("module_status_disabled")
        if rec.active:
            return "✓", tr("module_status_active")
        return "·", tr("module_status_disabled")

    def _open_folder(self, ctx: ModuleContext, folder: Path) -> None:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if ctx.launcher is not None and hasattr(ctx.launcher, "_open_path"):
                ctx.launcher._open_path(folder)
            elif os.name == "nt":
                os.startfile(str(folder))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as exc:
            ctx.message(tr("open_folder_title"), str(exc), error=True)

    def modules_screen(self, ctx: ModuleContext) -> None:
        if ctx.menu is None or ctx.registry is None:
            return
        while True:
            records = list(ctx.registry.records)
            builtin_records = [r for r in records if r.source == "builtin"]
            official_external = [r for r in records if r.source == "external" and r.official]
            unofficial_external = [r for r in records if r.source == "external" and not r.official]
            external_records = official_external + unofficial_external
            active_count = sum(1 for r in records if r.active and not r.error)

            items: list[MenuItem] = [
                MenuItem(tr("modules_reload"), ("reload", None)),
                MenuItem(tr("modules_open_folder"), ("open_folder", None)),
            ]
            if official_external:
                items.append(MenuItem(section_label(tr("modules_section_official")), selectable=False))
                for idx, rec in enumerate(official_external):
                    mark, status_str = self._status_text(rec)
                    label = f"{mark} ★ {rec.module.meta.display_name()}"
                    hint = f"{status_str} · {tr('module_badge_official')}"
                    items.append(MenuItem(label, ("record", ("official", idx)), hint=hint))

            if unofficial_external:
                items.append(MenuItem(section_label(tr("modules_section_unofficial")), selectable=False))
                for idx, rec in enumerate(unofficial_external):
                    mark, status_str = self._status_text(rec)
                    label = f"{mark} {rec.module.meta.display_name()}"
                    hint = f"{status_str} · {tr('module_badge_unofficial')}"
                    items.append(MenuItem(label, ("record", ("unofficial", idx)), hint=hint))

            if builtin_records:
                items.append(MenuItem(section_label(tr("modules_section_builtin")), selectable=False))
                for idx, rec in enumerate(builtin_records):
                    mark, status_str = self._status_text(rec)
                    label = f"{mark} {rec.module.meta.display_name()}"
                    items.append(MenuItem(label, ("record", ("builtin", idx)), hint=status_str))

            items.append(MenuItem(tr("back"), ("back", None)))
            subtitle = tr(
                "modules_subtitle",
                active=active_count,
                external=len(external_records),
                total=len(records),
            )
            action = ctx.menu.choose(
                tr("modules"),
                items,
                subtitle=subtitle,
                spacer_after_title=True,
                view_key="modules-manager",
            )
            if not action or action[0] == "back":
                return
            if action[0] == "reload":
                ctx.registry.reload_all(ctx)
                new_active = sum(1 for r in ctx.registry.records if r.active and not r.error)
                ctx.message(
                    tr("modules"),
                    tr("modules_reloaded_body", active=new_active, total=len(ctx.registry.records)),
                )
            elif action[0] == "open_folder":
                folders = ctx.registry.ensure_external_dirs()
                target = folders[0] if folders else (ctx.store.base / "modules" if ctx.store else Path("modules"))
                self._open_folder(ctx, target)
            elif action[0] == "record":
                group, idx = action[1]
                if group == "official":
                    pool = official_external
                elif group == "unofficial":
                    pool = unofficial_external
                else:
                    pool = builtin_records
                if 0 <= idx < len(pool):
                    self.module_detail_screen(ctx, pool[idx])

    def module_detail_screen(self, ctx: ModuleContext, rec: ModuleRecord) -> None:
        if ctx.menu is None or ctx.registry is None:
            return
        while True:
            _, status_str = self._status_text(rec)
            source_str = (
                tr("module_source_builtin")
                if rec.source == "builtin"
                else tr("module_source_external", file=rec.path.name if rec.path else "?")
            )
            if rec.source == "builtin":
                trust_str = tr("module_trust_core")
            elif rec.official:
                trust_str = tr("module_trust_official")
            else:
                trust_str = tr("module_trust_unofficial")

            lines = [
                f"{tr('module_id_label')}: {rec.id} · v{rec.module.meta.version} · {source_str}",
                f"{tr('module_author')}: {rec.module.meta.author} · {tr('module_status_label')}: {status_str}",
                f"{tr('module_trust_label')}: {trust_str}",
            ]
            if rec.sha256:
                lines.append(f"{tr('module_sha256_label')}: {rec.sha256[:16]}…")
            if rec.module.meta.replaces:
                lines.append(f"{tr('module_replaces_label')}: {rec.module.meta.replaces}")
            desc = rec.module.meta.display_description()
            if desc:
                lines.extend(["", desc])
            if not rec.module.meta.can_disable and rec.source == "builtin":
                lines.extend(["", tr("module_core_locked")])
            if rec.error:
                lines.extend(["", f"{tr('module_error_label')}: {rec.error}"])

            items: list[MenuItem] = []
            if rec.active and not rec.error and rec.id != "modules":
                if type(rec.module).open_screen is not LauncherModule.open_screen:
                    items.append(MenuItem(tr("module_open_screen"), "open"))
            if rec.module.meta.can_disable or rec.source == "external":
                toggle_label = tr("module_disable") if rec.enabled else tr("module_enable")
                items.append(MenuItem(toggle_label, "toggle"))
            items.append(MenuItem(tr("back"), "back"))

            title_prefix = "★ " if (rec.source == "external" and rec.official) else ""
            action = ctx.menu.choose(
                title_prefix + rec.module.meta.display_name(),
                items,
                subtitle="\n".join(lines),
                spacer_after_title=True,
                view_key=f"module-detail:{rec.disable_key}",
            )
            if not action or action == "back":
                return
            if action == "toggle":
                ctx.registry.set_record_enabled(rec, not rec.enabled, ctx)
            elif action == "open":
                rec.module.open_screen(ctx)
