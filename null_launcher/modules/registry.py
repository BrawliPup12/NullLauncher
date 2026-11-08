from __future__ import annotations

import dataclasses
import importlib.util
import inspect
import logging
import os
from pathlib import Path
import sys
from typing import Any, Callable, Optional

from ..config import APP_VERSION, register_translations, section_label, tr
from ..terminal import MenuItem
from .base import (
    LauncherModule,
    MenuContribution,
    ModuleContext,
    ModuleMetadata,
    SettingContribution,
    VersionSourceProvider,
)
from .trust import verify_official_module_bytes as _verify_official_module_bytes

MODULES_README = """# NullLauncher Modules

Drop `.py` files (single-file modules) or package folders (`my_module/__init__.py`)
into this directory. NullLauncher automatically discovers and loads them on startup
or when you click **Reload modules** in the **Modules** screen.

## Example 1: Adding a new module (e.g. Modrinth browser)

```python
from null_launcher.modules import LauncherModule, MenuContribution, ModuleContext, ModuleMetadata
from null_launcher.terminal import MenuItem


class ModrinthModule(LauncherModule):
    meta = ModuleMetadata(
        id="modrinth",
        name="Modrinth",
        description="Search and install content from Modrinth.",
        version="1.0.0",
        author="You",
        order=25,
    )
    translations = {
        "en": {"modrinth_title": "Modrinth Catalog"},
        "ru": {"modrinth_title": "Каталог Modrinth"},
    }

    def get_menu_items(self, ctx: ModuleContext) -> list[MenuContribution]:
        return [MenuContribution(id="modrinth", label=self.meta.display_name(), order=self.meta.order)]

    def open_screen(self, ctx: ModuleContext) -> None:
        ctx.menu.choose(
            ctx.tr("modrinth_title"),
            [MenuItem(ctx.tr("back"), "back")],
            subtitle="Your custom Modrinth screen works!",
        )
```

## Example 2: Replacing a built-in module (e.g. Versions)

Set `replaces="versions"` (or `id="versions"`) in `ModuleMetadata`. If you later disable
or remove your custom module, the built-in module is automatically restored.
"""


class _SimpleModuleAdapter(LauncherModule):
    """Wraps a plain Python file with MODULE_ID / functions into a LauncherModule."""

    def __init__(self, mod: Any, meta: ModuleMetadata):
        self._mod = mod
        self.meta = meta
        raw_tr = getattr(mod, "TRANSLATIONS", None) or getattr(mod, "translations", None)
        self.translations = raw_tr if isinstance(raw_tr, dict) else {}

    def on_load(self, ctx: ModuleContext) -> None:
        fn = getattr(self._mod, "on_load", None)
        if callable(fn):
            fn(ctx)

    def on_unload(self, ctx: ModuleContext) -> None:
        fn = getattr(self._mod, "on_unload", None)
        if callable(fn):
            fn(ctx)

    def get_header_lines(self, ctx: ModuleContext) -> list[str]:
        fn = getattr(self._mod, "get_header_lines", None)
        return list(fn(ctx)) if callable(fn) else []

    def get_menu_items(self, ctx: ModuleContext) -> list[MenuContribution]:
        fn = getattr(self._mod, "get_menu_items", None)
        if callable(fn):
            return list(fn(ctx))
        if callable(getattr(self._mod, "open_screen", None)):
            return [
                MenuContribution(
                    id=self.meta.id,
                    label=self.meta.display_name(),
                    order=self.meta.order,
                    module_id=self.meta.id,
                )
            ]
        return []

    def handle_action(self, ctx: ModuleContext, action: Any) -> bool:
        fn = getattr(self._mod, "handle_action", None)
        if callable(fn) and fn(ctx, action):
            return True
        return super().handle_action(ctx, action)

    def open_screen(self, ctx: ModuleContext) -> None:
        fn = getattr(self._mod, "open_screen", None)
        if callable(fn):
            fn(ctx)

    def get_version_providers(self, ctx: ModuleContext) -> list[VersionSourceProvider]:
        fn = getattr(self._mod, "get_version_providers", None)
        return list(fn(ctx)) if callable(fn) else []

    def on_before_launch(self, ctx: ModuleContext, version_id: str, options: dict[str, Any]) -> None:
        fn = getattr(self._mod, "on_before_launch", None)
        if callable(fn):
            fn(ctx, version_id, options)

    def on_modify_command(
        self,
        ctx: ModuleContext,
        version_id: str,
        command: list[str],
        options: dict[str, Any],
    ) -> list[str]:
        fn = getattr(self._mod, "on_modify_command", None)
        if callable(fn):
            res = fn(ctx, version_id, command, options)
            if isinstance(res, list):
                return res
        return command

    def on_after_launch(self, ctx: ModuleContext, version_id: str, exit_code: int) -> None:
        fn = getattr(self._mod, "on_after_launch", None)
        if callable(fn):
            fn(ctx, version_id, exit_code)


@dataclasses.dataclass
class ModuleRecord:
    module: LauncherModule
    source: str = "builtin"  # "builtin" | "external"
    path: Optional[Path] = None
    official: bool = False
    sha256: str = ""
    enabled: bool = True
    active: bool = False
    replaced_by: Optional[str] = None
    error: str = ""

    @property
    def id(self) -> str:
        return self.module.meta.id

    @property
    def disable_key(self) -> str:
        if self.source == "external" and self.path is not None:
            return f"ext:{self.path.stem}:{self.id}"
        return self.id

    @property
    def target_id(self) -> str:
        return str(self.module.meta.replaces or self.module.meta.id).strip()


class ModuleRegistry:
    """Discovers, resolves overrides, and fault-isolates all NullLauncher modules."""

    def __init__(
        self,
        log: Optional[logging.Logger] = None,
        external_dirs: Optional[list[Path]] = None,
    ):
        self.log = log or logging.getLogger("NullLauncher")
        self.external_dirs: list[Path] = list(external_dirs or [])
        self.records: list[ModuleRecord] = []
        self._builtin_factories: list[Callable[[], LauncherModule]] = []

    def register_builtin_factory(self, factory: Callable[[], LauncherModule]) -> None:
        self._builtin_factories.append(factory)

    def register(
        self,
        module: LauncherModule,
        *,
        source: str = "external",
        path: Optional[Path] = None,
        ctx: Optional[ModuleContext] = None,
    ) -> ModuleRecord:
        if getattr(module, "translations", None):
            register_translations(module.translations)
        rec = ModuleRecord(module=module, source=source, path=path)
        self.records.append(rec)
        if ctx is not None:
            self.resolve_and_activate(ctx)
        return rec

    def ensure_external_dirs(self) -> list[Path]:
        dirs: list[Path] = []
        seen: set[Path] = set()
        env_dir = os.environ.get("NULLLAUNCHER_MODULES_DIR", "").strip()
        candidates = list(self.external_dirs)
        if env_dir:
            candidates.append(Path(env_dir).expanduser())
        for folder in candidates:
            try:
                resolved = folder.expanduser().resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                resolved.mkdir(parents=True, exist_ok=True)
                readme = resolved / "README_MODULES.md"
                if not readme.exists():
                    readme.write_text(MODULES_README, encoding="utf-8")
                dirs.append(resolved)
            except Exception as exc:
                self.log.warning("Could not prepare modules folder %s: %s", folder, exc)
        return dirs

    def reload_all(self, ctx: ModuleContext) -> None:
        for rec in self.records:
            if rec.active:
                self._safe_call(rec, "on_unload", lambda r=rec: r.module.on_unload(ctx), default=None)
        self.records.clear()

        for factory in self._builtin_factories:
            try:
                mod = factory()
                mod.meta.builtin = True
                if getattr(mod, "translations", None):
                    register_translations(mod.translations)
                self.records.append(ModuleRecord(module=mod, source="builtin", path=None, official=True))
            except Exception as exc:
                self.log.exception("Failed to instantiate built-in module")
                broken_meta = ModuleMetadata(
                    id=getattr(factory, "__name__", "builtin_error"),
                    name=getattr(factory, "__name__", "Built-in Module"),
                    builtin=True,
                )
                broken_mod = LauncherModule()
                broken_mod.meta = broken_meta
                self.records.append(
                    ModuleRecord(
                        module=broken_mod,
                        source="builtin",
                        official=True,
                        enabled=False,
                        active=False,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )

        for folder in self.ensure_external_dirs():
            self._discover_in_folder(folder)

        self.resolve_and_activate(ctx)

    def _discover_in_folder(self, folder: Path) -> None:
        if not folder.is_dir():
            return
        entries: list[Path] = []
        try:
            for item in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
                if item.name.startswith(("_", ".")):
                    continue
                if item.is_file() and item.suffix.lower() == ".py":
                    entries.append(item)
                elif item.is_dir() and (item / "__init__.py").is_file():
                    entries.append(item / "__init__.py")
        except Exception as exc:
            self.log.warning("Could not scan external modules folder %s: %s", folder, exc)
            return

        for entry_path in entries:
            self._load_external_file(entry_path)

    def _load_external_file(self, file_path: Path) -> None:
        stem = file_path.parent.name if file_path.name == "__init__.py" else file_path.stem
        mod_name = f"null_launcher_ext_{stem}_{abs(hash(str(file_path))) & 0xFFFFFF:06x}"
        try:
            # Read raw bytes BEFORE executing external module code so the file cannot
            # self-modify on disk or monkeypatch the cryptographic verification table.
            pre_exec_bytes = file_path.read_bytes()
            spec = importlib.util.spec_from_file_location(mod_name, str(file_path))
            if spec is None or spec.loader is None:
                raise RuntimeError(f"Could not create module spec for {file_path.name}")
            py_mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = py_mod
            spec.loader.exec_module(py_mod)
            loaded_modules = self._extract_modules_from_py(py_mod, stem)
            if not loaded_modules:
                raise RuntimeError("No LauncherModule class, create_module(), or MODULE_ID found in file")
            for instance in loaded_modules:
                instance.meta.builtin = False
                is_official, digest = _verify_official_module_bytes(instance.meta.id, pre_exec_bytes)
                if getattr(instance, "translations", None):
                    register_translations(instance.translations)
                self.records.append(
                    ModuleRecord(
                        module=instance,
                        source="external",
                        path=file_path,
                        official=is_official,
                        sha256=digest,
                    )
                )
        except Exception as exc:
            self.log.warning("External module %s failed to load: %s", file_path, exc)
            broken = LauncherModule()
            broken.meta = ModuleMetadata(
                id=f"broken:{stem}",
                name=stem,
                description=str(file_path.name),
                version="?",
                author="external",
                builtin=False,
            )
            self.records.append(
                ModuleRecord(
                    module=broken,
                    source="external",
                    path=file_path,
                    enabled=False,
                    active=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )

    def _extract_modules_from_py(self, py_mod: Any, default_stem: str) -> list[LauncherModule]:
        out: list[LauncherModule] = []

        for factory_name in ("create_module", "register_module", "get_module"):
            fn = getattr(py_mod, factory_name, None)
            if callable(fn):
                res = fn()
                if isinstance(res, LauncherModule):
                    out.append(res)
                elif isinstance(res, (list, tuple)):
                    out.extend(x for x in res if isinstance(x, LauncherModule))
                if out:
                    return out

        for attr_name in ("MODULE", "MODULES"):
            val = getattr(py_mod, attr_name, None)
            if isinstance(val, LauncherModule):
                return [val]
            if isinstance(val, (list, tuple)):
                found = [x for x in val if isinstance(x, LauncherModule)]
                if found:
                    return found

        for _, obj in inspect.getmembers(py_mod, inspect.isclass):
            if issubclass(obj, LauncherModule) and obj is not LauncherModule and obj is not _SimpleModuleAdapter:
                if obj.__module__ == py_mod.__name__:
                    out.append(obj())
        if out:
            return out

        mod_id = getattr(py_mod, "MODULE_ID", None)
        if mod_id or callable(getattr(py_mod, "open_screen", None)):
            meta = ModuleMetadata(
                id=str(mod_id or default_stem).strip(),
                name=str(getattr(py_mod, "MODULE_NAME", default_stem)),
                description=str(getattr(py_mod, "MODULE_DESCRIPTION", "")),
                version=str(getattr(py_mod, "MODULE_VERSION", APP_VERSION)),
                author=str(getattr(py_mod, "MODULE_AUTHOR", "External")),
                replaces=getattr(py_mod, "MODULE_REPLACES", None),
                order=int(getattr(py_mod, "MODULE_ORDER", 55)),
                builtin=False,
            )
            return [_SimpleModuleAdapter(py_mod, meta)]
        return []

    def is_record_enabled(self, rec: ModuleRecord, ctx: ModuleContext) -> bool:
        if rec.error:
            return False
        if not rec.module.meta.can_disable and rec.source == "builtin":
            return True
        if ctx.store is None:
            return True
        disabled = set(ctx.store.disabled_modules())
        if rec.disable_key in disabled:
            return False
        if rec.source == "builtin" and rec.id in disabled:
            return False
        if rec.source == "external" and rec.id in disabled:
            has_builtin_same_id = any(r.source == "builtin" and r.id == rec.id for r in self.records)
            if not has_builtin_same_id:
                return False
        return True

    def set_record_enabled(self, rec: ModuleRecord, enabled: bool, ctx: ModuleContext) -> None:
        if ctx.store is None:
            return
        if not rec.module.meta.can_disable and rec.source == "builtin" and not enabled:
            return
        ctx.store.set_module_enabled(rec.disable_key, enabled)
        if rec.disable_key != rec.id:
            has_builtin_same_id = any(r.source == "builtin" and r.id == rec.id for r in self.records)
            if not has_builtin_same_id:
                ctx.store.set_module_enabled(rec.id, enabled)
        self.resolve_and_activate(ctx)

    def resolve_and_activate(self, ctx: ModuleContext) -> None:
        previously_active = {id(rec): rec.active for rec in self.records}
        for rec in self.records:
            rec.enabled = self.is_record_enabled(rec, ctx)
            rec.active = False
            rec.replaced_by = None

        # Group enabled records by the target slot they occupy (either replaces or id).
        # Later/external modules override earlier/built-in modules for the same target slot.
        slot_winner: dict[str, ModuleRecord] = {}
        for rec in self.records:
            if not rec.enabled or rec.error:
                continue
            target = rec.target_id
            prev = slot_winner.get(target)
            if prev is None:
                slot_winner[target] = rec
            else:
                # External module wins over built-in; later external wins over earlier external
                if rec.source == "external" or prev.source == "builtin":
                    prev.replaced_by = f"{rec.module.meta.display_name()} ({rec.id})"
                    slot_winner[target] = rec
                else:
                    rec.replaced_by = f"{prev.module.meta.display_name()} ({prev.id})"

        # Also check if any winning module has `replaces="X"` where X had its own winner under slot "X"
        for rec in list(slot_winner.values()):
            for other in self.records:
                if other is not rec and other.enabled and not other.error:
                    if other.id == rec.target_id or other.target_id == rec.target_id:
                        if slot_winner.get(rec.target_id) is rec:
                            other.replaced_by = f"{rec.module.meta.display_name()} ({rec.id})"

        for rec in self.records:
            winner = slot_winner.get(rec.target_id)
            rec.active = (winner is rec) and rec.enabled and not rec.error
            was_active = previously_active.get(id(rec), False)
            if rec.active and not was_active:
                self._safe_call(rec, "on_load", lambda r=rec: r.module.on_load(ctx), default=None)
            elif was_active and not rec.active:
                self._safe_call(rec, "on_unload", lambda r=rec: r.module.on_unload(ctx), default=None)

    def active_records(self) -> list[ModuleRecord]:
        active = [r for r in self.records if r.active and not r.error]
        active.sort(key=lambda r: (r.module.meta.order, r.id))
        return active

    def active_modules(self) -> list[LauncherModule]:
        return [r.module for r in self.active_records()]

    def get_active_module(self, slot_or_id: str) -> Optional[LauncherModule]:
        for rec in self.active_records():
            if rec.id == slot_or_id or rec.target_id == slot_or_id:
                return rec.module
        return None

    def is_replaced(self, slot_id: str) -> bool:
        for rec in self.active_records():
            if rec.target_id == slot_id and (rec.source == "external" or rec.module.meta.replaces == slot_id):
                return True
        return False

    def _safe_call(self, rec: ModuleRecord, hook_name: str, fn: Callable[[], Any], default: Any) -> Any:
        try:
            return fn()
        except SystemExit:
            raise
        except Exception as exc:
            self.log.exception("Module '%s' failed in %s", rec.id, hook_name)
            rec.error = f"{hook_name}: {type(exc).__name__}: {exc}"
            return default

    def collect_header_lines(self, ctx: ModuleContext) -> list[str]:
        lines: list[str] = []
        for rec in self.active_records():
            part = self._safe_call(rec, "get_header_lines", lambda r=rec: r.module.get_header_lines(ctx), default=[])
            if isinstance(part, list):
                lines.extend(str(x) for x in part if x)
        return lines

    def build_main_menu(self, ctx: ModuleContext) -> tuple[list[MenuItem], Optional[MenuItem]]:
        contributions: list[MenuContribution] = []
        for rec in self.active_records():
            items = self._safe_call(rec, "get_menu_items", lambda r=rec: r.module.get_menu_items(ctx), default=[])
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, MenuContribution):
                        if not item.module_id:
                            item.module_id = rec.id
                        contributions.append(item)

        main_contribs = [c for c in contributions if c.location == "main"]
        bottom_contribs = [c for c in contributions if c.location == "bottom"]
        main_contribs.sort(key=lambda c: c.order)
        bottom_contribs.sort(key=lambda c: c.order)

        menu_items: list[MenuItem] = []
        last_section: Optional[str] = None
        for c in main_contribs:
            if c.section and c.section != last_section:
                menu_items.append(MenuItem(section_label(c.section), selectable=False))
                last_section = c.section
            elif not c.section:
                last_section = None
            menu_items.append(c.to_menu_item())

        menu_items.append(MenuItem(tr("exit"), "exit"))
        bottom_item = bottom_contribs[0].to_menu_item() if bottom_contribs else None
        return menu_items, bottom_item

    def dispatch_action(self, ctx: ModuleContext, action: Any) -> bool:
        for rec in self.active_records():
            items = self._safe_call(rec, "get_menu_items", lambda r=rec: r.module.get_menu_items(ctx), default=[])
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, MenuContribution) and item.id == action and callable(item.handler):
                        item.handler(ctx)
                        return True
            if rec.module.handle_action(ctx, action):
                return True
        return False

    def collect_settings_items(self, ctx: ModuleContext) -> list[SettingContribution]:
        out: list[SettingContribution] = []
        for rec in self.active_records():
            items = self._safe_call(rec, "get_settings_items", lambda r=rec: r.module.get_settings_items(ctx), default=[])
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, SettingContribution):
                        if not item.module_id:
                            item.module_id = rec.id
                        out.append(item)
        out.sort(key=lambda s: s.order)
        return out

    def dispatch_settings_action(self, ctx: ModuleContext, action: Any) -> bool:
        for item in self.collect_settings_items(ctx):
            if item.id == action and callable(item.handler):
                item.handler(ctx)
                return True
        for rec in self.active_records():
            handled = self._safe_call(
                rec,
                "handle_settings_action",
                lambda r=rec: r.module.handle_settings_action(ctx, action),
                default=False,
            )
            if handled:
                return True
        return False

    def collect_version_providers(self, ctx: ModuleContext) -> list[VersionSourceProvider]:
        providers: list[VersionSourceProvider] = []
        for rec in self.active_records():
            res = self._safe_call(
                rec,
                "get_version_providers",
                lambda r=rec: r.module.get_version_providers(ctx),
                default=[],
            )
            if isinstance(res, list):
                providers.extend(p for p in res if isinstance(p, VersionSourceProvider))
        return providers

    def collect_preload_tasks(self, ctx: ModuleContext) -> list[tuple[str, Callable[[], None]]]:
        tasks: list[tuple[str, Callable[[], None]]] = []
        for rec in self.active_records():
            res = self._safe_call(
                rec,
                "get_preload_tasks",
                lambda r=rec: r.module.get_preload_tasks(ctx),
                default=[],
            )
            if isinstance(res, list):
                for entry in res:
                    if isinstance(entry, tuple) and len(entry) == 2 and callable(entry[1]):
                        label, fn = str(entry[0]), entry[1]
                        tasks.append(
                            (
                                label,
                                lambda r=rec, f=fn: self._safe_call(r, "preload_task", f, default=None),
                            )
                        )
        for provider in self.collect_version_providers(ctx):
            tasks.append(
                (
                    getattr(provider, "id", "provider"),
                    lambda p=provider: p.preload(ctx, lambda _msg: None),
                )
            )
        return tasks

    def run_before_launch(self, ctx: ModuleContext, version_id: str, options: dict[str, Any]) -> None:
        for rec in self.active_records():
            self._safe_call(
                rec,
                "on_before_launch",
                lambda r=rec: r.module.on_before_launch(ctx, version_id, options),
                default=None,
            )

    def run_modify_command(
        self,
        ctx: ModuleContext,
        version_id: str,
        command: list[str],
        options: dict[str, Any],
    ) -> list[str]:
        current = list(command)
        for rec in self.active_records():
            updated = self._safe_call(
                rec,
                "on_modify_command",
                lambda r=rec, cmd=current: r.module.on_modify_command(ctx, version_id, list(cmd), options),
                default=current,
            )
            if isinstance(updated, list) and updated:
                current = updated
        return current

    def run_after_launch(self, ctx: ModuleContext, version_id: str, exit_code: int) -> None:
        for rec in self.active_records():
            self._safe_call(
                rec,
                "on_after_launch",
                lambda r=rec: r.module.on_after_launch(ctx, version_id, exit_code),
                default=None,
            )
