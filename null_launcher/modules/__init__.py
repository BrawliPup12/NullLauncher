from __future__ import annotations

from .accounts_module import AccountsModule
from .base import (
    LauncherModule,
    MenuContribution,
    ModuleContext,
    ModuleMetadata,
    SettingContribution,
    VersionSourceProvider,
)
from .modules_module import ModulesManagerModule
from .play_module import PlayModule
from .registry import ModuleRecord, ModuleRegistry
from .settings_module import SettingsModule
from .trust import OFFICIAL_MODULE_DIGESTS, verify_official_module_bytes
from .versions_module import VersionsModule

BUILTIN_MODULE_CLASSES: tuple[type[LauncherModule], ...] = (
    PlayModule,
    VersionsModule,
    AccountsModule,
    ModulesManagerModule,
    SettingsModule,
)


def create_default_registry(
    *,
    log=None,
    external_dirs=None,
) -> ModuleRegistry:
    registry = ModuleRegistry(log=log, external_dirs=external_dirs)
    for cls in BUILTIN_MODULE_CLASSES:
        registry.register_builtin_factory(cls)
    return registry


__all__ = [
    "AccountsModule",
    "BUILTIN_MODULE_CLASSES",
    "LauncherModule",
    "MenuContribution",
    "ModuleContext",
    "ModuleMetadata",
    "ModuleRecord",
    "ModuleRegistry",
    "ModulesManagerModule",
    "OFFICIAL_MODULE_DIGESTS",
    "PlayModule",
    "SettingContribution",
    "SettingsModule",
    "VersionSourceProvider",
    "VersionsModule",
    "create_default_registry",
    "verify_official_module_bytes",
]
