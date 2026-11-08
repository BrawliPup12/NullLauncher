from __future__ import annotations

from typing import Any

from .. import config as cfg
from ..config import APP_VERSION, DIM, RESET, tr
from ..utils import portable_mode_enabled
from .base import LauncherModule, MenuContribution, ModuleContext, ModuleMetadata


class PlayModule(LauncherModule):
    meta = ModuleMetadata(
        id="play",
        name_key="play",
        description_key="module_play_desc",
        order=10,
        can_disable=True,
        builtin=True,
    )

    def get_header_lines(self, ctx: ModuleContext) -> list[str]:
        if ctx.store is None:
            return []
        account = ctx.store.account()
        selected = ctx.store.data.get("selected_version") or tr("not_selected")
        acc_name = account["name"] if account else tr("not_selected")
        mode_suffix = f" · {tr('portable_mode')}" if portable_mode_enabled() else ""
        header = [
            f"{cfg.SUBTITLE_COLOR}{DIM}NullLauncher v{APP_VERSION}{mode_suffix}{RESET}",
            f"{cfg.SUBTITLE_COLOR}{DIM}{tr('account')}:{RESET} {acc_name}    {cfg.SUBTITLE_COLOR}{DIM}{tr('version')}:{RESET} {selected}",
        ]
        last_launch = ctx.store.last_launch()
        if last_launch.get("version"):
            stamp = str(last_launch.get("launched_at") or "").replace("T", " ").replace("Z", "")[:19]
            header.append(
                f"{cfg.SUBTITLE_COLOR}{DIM}{tr('last_played')}:{RESET} {last_launch['version']}"
                + (f" · {stamp}" if stamp else "")
            )
        return header

    def get_menu_items(self, ctx: ModuleContext) -> list[MenuContribution]:
        items: list[MenuContribution] = [
            MenuContribution(
                id="play",
                label=tr("play"),
                order=10,
                module_id=self.meta.id,
            )
        ]
        if ctx.store is not None:
            last_launch = ctx.store.last_launch()
            quick_versions: list[tuple[str, str]] = []
            if last_launch.get("version"):
                quick_versions.append(
                    (tr("quick_play_last", version=last_launch["version"]), str(last_launch["version"]))
                )
            for favorite in ctx.store.favorite_versions():
                if favorite != last_launch.get("version"):
                    quick_versions.append((tr("quick_play_favorite", version=favorite), favorite))
            for idx, (label, version) in enumerate(quick_versions[:4]):
                items.append(
                    MenuContribution(
                        id=("quick-play", version),
                        label=label,
                        order=11 + idx,
                        section=tr("quick_play_section"),
                        module_id=self.meta.id,
                    )
                )
        return items

    def handle_action(self, ctx: ModuleContext, action: Any) -> bool:
        if action == "play":
            self.open_screen(ctx)
            return True
        if isinstance(action, tuple) and len(action) == 2 and action[0] == "quick-play":
            if ctx.store is not None:
                ctx.store.data["selected_version"] = str(action[1])
                ctx.store.save()
            self.open_screen(ctx)
            return True
        return False

    def open_screen(self, ctx: ModuleContext) -> None:
        if ctx.launcher is not None and hasattr(ctx.launcher, "_execute_play"):
            ctx.launcher._execute_play()
        elif ctx.launcher is not None and hasattr(ctx.launcher, "play"):
            ctx.launcher.play()
