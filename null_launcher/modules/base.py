from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Any, Callable, Optional

from ..config import APP_VERSION, tr
from ..terminal import MenuItem

if TYPE_CHECKING:
    from ..catalog import LauncherData, VersionEntry
    from ..download_cache import DownloadCache
    from ..state import StateStore
    from ..terminal import Menu, Terminal
    from .registry import ModuleRegistry


@dataclasses.dataclass
class ModuleMetadata:
    id: str
    name_key: str = ""
    description_key: str = ""
    name: str = ""
    description: str = ""
    version: str = APP_VERSION
    author: str = "NullLauncher"
    replaces: Optional[str] = None
    order: int = 100
    can_disable: bool = True
    builtin: bool = False

    def display_name(self) -> str:
        if self.name_key:
            translated = tr(self.name_key)
            if translated and translated != self.name_key:
                return translated
        return self.name or self.id

    def display_description(self) -> str:
        if self.description_key:
            translated = tr(self.description_key)
            if translated and translated != self.description_key:
                return translated
        return self.description or ""


@dataclasses.dataclass
class MenuContribution:
    id: Any
    label: str
    order: int = 100
    location: str = "main"
    section: Optional[str] = None
    selectable: bool = True
    hint: str = ""
    module_id: str = ""
    handler: Optional[Callable[[ModuleContext], None]] = None

    def to_menu_item(self) -> MenuItem:
        return MenuItem(
            self.label,
            self.id,
            selectable=self.selectable,
            hint=self.hint,
        )


@dataclasses.dataclass
class SettingContribution:
    id: Any
    label: str
    section_key: str = "settings_modules_custom"
    order: int = 100
    hint: str = ""
    module_id: str = ""
    handler: Optional[Callable[[ModuleContext], None]] = None


class VersionSourceProvider:
    """Extension point for modules that add version/modpack sources to the catalog."""

    id: str = "custom"

    def filter_tabs(self, ctx: ModuleContext) -> list[tuple[str, str]]:
        return []

    def preload(self, ctx: ModuleContext, progress: Callable[[str], None]) -> None:
        return None

    def build_entries(self, ctx: ModuleContext, filter_kind: str, query: str) -> list[VersionEntry]:
        return []

    def can_handle(self, ctx: ModuleContext, entry: VersionEntry) -> bool:
        return False

    def install(self, ctx: ModuleContext, entry: VersionEntry) -> Optional[str]:
        return None

    def delete(self, ctx: ModuleContext, entry: VersionEntry) -> Optional[bool]:
        return None


class ModuleContext:
    """Execution context passed to all module methods and hooks."""

    def __init__(
        self,
        launcher: Any,
        store: Optional[StateStore] = None,
        term: Optional[Terminal] = None,
        menu: Optional[Menu] = None,
        data: Optional[LauncherData] = None,
        mll: Any = None,
        log: Optional[logging.Logger] = None,
        download_cache: Optional[DownloadCache] = None,
        registry: Optional[ModuleRegistry] = None,
    ):
        self.launcher = launcher
        self.store = store if store is not None else getattr(launcher, "store", None)
        self.term = term if term is not None else getattr(launcher, "term", None)
        self.menu = menu if menu is not None else getattr(launcher, "menu", None)
        self.data = data if data is not None else getattr(launcher, "data", None)
        self.mll = mll if mll is not None else getattr(launcher, "mll", None)
        self.log = log if log is not None else getattr(launcher, "log", logging.getLogger("NullLauncher"))
        self.download_cache = download_cache if download_cache is not None else getattr(launcher, "download_cache", None)
        self.registry = registry if registry is not None else getattr(launcher, "modules", None)

    def tr(self, key: str, **values: Any) -> str:
        return tr(key, **values)

    def message(self, title: str, text: str, *, error: bool = False) -> None:
        if self.launcher is not None and hasattr(self.launcher, "message"):
            self.launcher.message(title, text, error=error)

    def confirm(self, title: str, text: str) -> bool:
        if self.launcher is not None and hasattr(self.launcher, "confirm"):
            return bool(self.launcher.confirm(title, text))
        return False

    def prompt(self, label: str, default: str = "") -> str:
        if self.term is not None and hasattr(self.term, "prompt"):
            return str(self.term.prompt(label, default))
        return str(default)

    def progress_task(self, title: str, fn: Callable[[dict[str, Callable]], Any]) -> Any:
        if (
            self.launcher is not None
            and hasattr(self.launcher, "progress_task")
            and self.term is not None
            and hasattr(self.term, "size")
            and hasattr(self.term, "render")
        ):
            return self.launcher.progress_task(title, fn)
        callbacks = {
            "setStatus": lambda _v: None,
            "setProgress": lambda _v: None,
            "setMax": lambda _v: None,
        }
        return fn(callbacks)

    def module_state(self, module_id: str) -> dict[str, Any]:
        if self.store is not None and hasattr(self.store, "module_state"):
            return self.store.module_state(module_id)
        return {}

    def save_state(self) -> None:
        if self.store is not None and hasattr(self.store, "save"):
            self.store.save()


class LauncherModule:
    """Base class for all built-in and external NullLauncher modules."""

    meta: ModuleMetadata = ModuleMetadata(id="unnamed", name="Unnamed Module")
    translations: dict[str, dict[str, str]] = {}

    def on_load(self, ctx: ModuleContext) -> None:
        pass

    def on_unload(self, ctx: ModuleContext) -> None:
        pass

    def on_first_run(self, ctx: ModuleContext) -> None:
        pass

    def get_preload_tasks(self, ctx: ModuleContext) -> list[tuple[str, Callable[[], None]]]:
        return []

    def get_header_lines(self, ctx: ModuleContext) -> list[str]:
        return []

    def get_menu_items(self, ctx: ModuleContext) -> list[MenuContribution]:
        return []

    def handle_action(self, ctx: ModuleContext, action: Any) -> bool:
        if action == self.meta.id:
            self.open_screen(ctx)
            return True
        return False

    def open_screen(self, ctx: ModuleContext) -> None:
        pass

    def get_settings_items(self, ctx: ModuleContext) -> list[SettingContribution]:
        return []

    def handle_settings_action(self, ctx: ModuleContext, action: Any) -> bool:
        return False

    def get_version_providers(self, ctx: ModuleContext) -> list[VersionSourceProvider]:
        return []

    def on_before_launch(self, ctx: ModuleContext, version_id: str, options: dict[str, Any]) -> None:
        pass

    def on_modify_command(
        self,
        ctx: ModuleContext,
        version_id: str,
        command: list[str],
        options: dict[str, Any],
    ) -> list[str]:
        return command

    def on_after_launch(self, ctx: ModuleContext, version_id: str, exit_code: int) -> None:
        pass
