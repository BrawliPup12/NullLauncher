from __future__ import annotations

import ast
import hashlib
import logging
from pathlib import Path
import re
import shutil
from typing import Any

from null_launcher.app import NullLauncher
from null_launcher.catalog import LauncherData, VersionEntry
from null_launcher.config import I18N, LANGUAGES, tr
from null_launcher.modules import (
    BUILTIN_MODULE_CLASSES,
    LauncherModule,
    MenuContribution,
    ModuleContext,
    ModuleMetadata,
    SettingContribution,
    VersionSourceProvider,
    create_default_registry,
)
from null_launcher.state import StateStore

OFFICIAL_MODULES_DIR = Path(__file__).resolve().parents[1] / "official_modules"
COMMUNITY_MODULES_DIR = Path(__file__).resolve().parents[1] / "community_modules"


class DummyTerm:
    def __init__(self):
        self._last_frame = []
        self._last_graphic_key = None
        self._sixel_cache = {}
        self._view_key = ""

    def prompt(self, label: str, default: str = "") -> str:
        return default


class DummyMenu:
    def __init__(self, actions: list[Any] | None = None):
        self.actions = list(actions or ["back"])
        self.calls: list[dict[str, Any]] = []

    def choose(self, title: str, items: list[Any], **kwargs: Any) -> Any:
        self.calls.append({"title": title, "items": items, **kwargs})
        if self.actions:
            return self.actions.pop(0)
        return "back"


def _make_launcher(tmp_path: Path, actions: list[Any] | None = None) -> NullLauncher:
    store = StateStore(tmp_path)
    term = DummyTerm()
    log = logging.getLogger("NullLauncherTest")
    launcher = object.__new__(NullLauncher)
    launcher.store = store
    launcher.term = term
    launcher.menu = DummyMenu(actions)
    launcher.mll = None
    launcher.log = log
    launcher.data = LauncherData(store, log)
    launcher.running = True
    launcher._news_image_cache = {}
    launcher.download_cache = None
    launcher.modules = create_default_registry(log=log, external_dirs=[tmp_path / "modules"])
    launcher.modules.reload_all(launcher.module_context())
    return launcher


def test_default_builtin_modules_are_lean_core_only(tmp_path):
    launcher = _make_launcher(tmp_path)
    active_ids = [m.meta.id for m in launcher.modules.active_modules()]
    expected = [cls.meta.id for cls in BUILTIN_MODULE_CLASSES]
    assert active_ids == expected
    assert active_ids == ["play", "versions", "accounts", "modules", "settings"]

    ctx = launcher.module_context()
    header = launcher.modules.collect_header_lines(ctx)
    assert any("NullLauncher" in line for line in header)

    main_items, bottom_item = launcher.modules.build_main_menu(ctx)
    values = [item.value for item in main_items if item.selectable]
    assert "play" in values
    assert "versions" in values
    assert "accounts" in values
    assert "modules" in values
    assert "settings" in values
    assert "exit" in values
    # External official modules (modrinth, proxies, news) are not embedded by default
    assert "modrinth" not in values
    assert "proxies" not in values
    assert bottom_item is None


def test_official_external_modules_are_verified_and_unforgeable(tmp_path):
    ext_dir = tmp_path / "modules"
    ext_dir.mkdir(parents=True, exist_ok=True)
    for name in ("news.py", "proxies.py"):
        shutil.copy2(OFFICIAL_MODULES_DIR / name, ext_dir / name)
    shutil.copy2(COMMUNITY_MODULES_DIR / "modrinth.py", ext_dir / "modrinth.py")

    # Also drop a third-party module that tries every trick to spoof being official
    (ext_dir / "fake_official.py").write_text(
        """
from null_launcher.modules import LauncherModule, ModuleMetadata
from null_launcher.modules import trust

# Attempt to mutate OFFICIAL_MODULE_DIGESTS at import time
try:
    trust.OFFICIAL_MODULE_DIGESTS["fake_official"] = "00" * 32
except Exception:
    pass

class FakeOfficialModule(LauncherModule):
    meta = ModuleMetadata(
        id="fake_official",
        name="Spoofed Module",
        author="NullLauncher",
        order=35,
    )
""",
        encoding="utf-8",
    )

    launcher = _make_launcher(tmp_path)
    ctx = launcher.module_context()

    for official_id in ("news", "proxies"):
        rec = next(r for r in launcher.modules.records if r.id == official_id)
        assert rec.source == "external"
        assert rec.official is True, f"{official_id} should be verified as official"
        assert rec.active is True
        assert len(rec.sha256) == 64

    # Modrinth is not in the official modules list -> must be marked unofficial
    modrinth_rec = next(r for r in launcher.modules.records if r.id == "modrinth")
    assert modrinth_rec.source == "external"
    assert modrinth_rec.official is False

    fake_rec = next(r for r in launcher.modules.records if r.id == "fake_official")
    assert fake_rec.source == "external"
    assert fake_rec.official is False

    # Now tamper with 1 byte of proxies.py (e.g. add a comment) and reload:
    tampered = (ext_dir / "proxies.py").read_text(encoding="utf-8") + "\n# tampered\n"
    (ext_dir / "proxies.py").write_text(tampered, encoding="utf-8")
    launcher.modules.reload_all(ctx)

    tampered_rec = next(r for r in launcher.modules.records if r.id == "proxies")
    assert tampered_rec.official is False


def test_preload_skips_news_when_news_module_is_absent(tmp_path, monkeypatch):
    import null_launcher.catalog as catalog_mod

    launcher = _make_launcher(tmp_path)
    news_called = []
    monkeypatch.setattr(
        catalog_mod,
        "fetch_official_minecraft_news",
        lambda **kw: news_called.append(True) or [],
    )

    class DummyMll:
        class utils:
            @staticmethod
            def get_version_list():
                return []

            @staticmethod
            def get_installed_versions(_mc_dir):
                return []

        class fabric:
            @staticmethod
            def get_all_minecraft_versions():
                return []

        class quilt:
            @staticmethod
            def get_all_minecraft_versions():
                return []

        class forge:
            @staticmethod
            def list_forge_versions():
                return []

    launcher.data.preload(DummyMll, lambda *_: None, load_news_module_active=False)
    assert news_called == []


def test_custom_modrinth_module_adds_menu_translations_and_version_provider(tmp_path):
    launcher = _make_launcher(tmp_path)

    class ModrinthProvider(VersionSourceProvider):
        id = "modrinth"

        def filter_tabs(self, ctx: ModuleContext) -> list[tuple[str, str]]:
            return [("modrinth", "Modrinth")]

        def build_entries(self, ctx: ModuleContext, filter_kind: str, query: str) -> list[VersionEntry]:
            if filter_kind not in ("all", "modrinth"):
                return []
            return [
                VersionEntry(
                    key="modrinth:cobblemon-1.21.1",
                    label="Modrinth Cobblemon 1.21.1",
                    kind="modpack",
                    loader_id="modrinth",
                    mc_version="1.21.1",
                    version_type="release",
                    installed=False,
                )
            ]

        def can_handle(self, ctx: ModuleContext, entry: VersionEntry) -> bool:
            return entry.loader_id == "modrinth"

        def install(self, ctx: ModuleContext, entry: VersionEntry) -> str:
            return "cobblemon-1.21.1-installed"

    class ModrinthModule(LauncherModule):
        meta = ModuleMetadata(
            id="modrinth",
            name_key="modrinth_menu",
            description="Modrinth integration",
            order=25,
        )
        translations = {
            "en": {"modrinth_menu": "Modrinth Mods"},
            "ru": {"modrinth_menu": "Моды Modrinth"},
        }

        def get_menu_items(self, ctx: ModuleContext) -> list[MenuContribution]:
            return [MenuContribution(id="modrinth", label=tr("modrinth_menu"), order=self.meta.order)]

        def get_version_providers(self, ctx: ModuleContext) -> list[VersionSourceProvider]:
            return [ModrinthProvider()]

    ctx = launcher.module_context()
    launcher.modules.register(ModrinthModule(), source="external", ctx=ctx)

    for lang in LANGUAGES:
        assert "modrinth_menu" in I18N[lang]

    main_items, _ = launcher.modules.build_main_menu(ctx)
    values = [item.value for item in main_items if item.selectable]
    assert "modrinth" in values

    launcher.menu = DummyMenu([("filter", None), ("back", None)])
    launcher.versions_screen()
    first_call_items = launcher.menu.calls[0]["items"]
    assert any("Modrinth Cobblemon 1.21.1" in item.label for item in first_call_items)


def test_replacing_builtin_module_and_restoring_on_disable(tmp_path):
    ext_dir = tmp_path / "modules"
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / "custom_versions.py").write_text(
        """
from null_launcher.modules import LauncherModule, MenuContribution, ModuleContext, ModuleMetadata

class CustomVersionsModule(LauncherModule):
    meta = ModuleMetadata(
        id="custom_versions",
        name="Custom Versions UI",
        replaces="versions",
        order=20,
    )

    def get_menu_items(self, ctx: ModuleContext) -> list[MenuContribution]:
        return [MenuContribution(id="versions", label="Custom Versions", order=20)]

    def open_screen(self, ctx: ModuleContext) -> None:
        ctx.module_state("custom_versions")["opened"] = True
        ctx.save_state()
""",
        encoding="utf-8",
    )

    launcher = _make_launcher(tmp_path)
    ctx = launcher.module_context()

    assert launcher.modules.is_replaced("versions") is True
    active_ver_mod = launcher.modules.get_active_module("versions")
    assert active_ver_mod is not None
    assert active_ver_mod.meta.id == "custom_versions"

    builtin_rec = next(r for r in launcher.modules.records if r.source == "builtin" and r.id == "versions")
    ext_rec = next(r for r in launcher.modules.records if r.source == "external" and r.id == "custom_versions")
    assert builtin_rec.active is False
    assert builtin_rec.replaced_by is not None
    assert ext_rec.active is True

    launcher.versions_screen()
    assert launcher.store.module_state("custom_versions").get("opened") is True

    # Disabling the external replacement automatically restores the built-in Versions module!
    launcher.modules.set_record_enabled(ext_rec, False, ctx)
    assert launcher.modules.is_replaced("versions") is False
    restored_mod = launcher.modules.get_active_module("versions")
    assert restored_mod is not None
    assert restored_mod.meta.id == "versions"
    assert builtin_rec.active is True
    assert builtin_rec.replaced_by is None


def test_broken_external_module_is_isolated_and_does_not_break_launcher(tmp_path):
    ext_dir = tmp_path / "modules"
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / "broken_plugin.py").write_text("raise RuntimeError('boom during import')\n", encoding="utf-8")
    (ext_dir / "simple_plugin.py").write_text(
        """
MODULE_ID = "simple_tool"
MODULE_NAME = "Simple Tool"

def open_screen(ctx):
    ctx.module_state("simple_tool")["ran"] = True

def on_before_launch(ctx, version_id, options):
    options.setdefault("jvmArguments", []).append("-Dsimple.plugin=true")
""",
        encoding="utf-8",
    )

    launcher = _make_launcher(tmp_path)
    ctx = launcher.module_context()

    broken_rec = next(r for r in launcher.modules.records if r.path and r.path.name == "broken_plugin.py")
    assert broken_rec.active is False
    assert "RuntimeError" in broken_rec.error

    simple_mod = launcher.modules.get_active_module("simple_tool")
    assert simple_mod is not None
    assert launcher.modules.dispatch_action(ctx, "simple_tool") is True
    assert launcher.store.module_state("simple_tool").get("ran") is True

    options: dict[str, Any] = {"jvmArguments": []}
    launcher.modules.run_before_launch(ctx, "1.21.4", options)
    assert "-Dsimple.plugin=true" in options["jvmArguments"]


def test_failing_hook_inside_active_module_is_caught_safely(tmp_path):
    launcher = _make_launcher(tmp_path)
    ctx = launcher.module_context()

    class CrashingHookModule(LauncherModule):
        meta = ModuleMetadata(id="crasher", name="Crasher")

        def get_header_lines(self, ctx: ModuleContext) -> list[str]:
            raise ValueError("header boom")

        def on_before_launch(self, ctx: ModuleContext, version_id: str, options: dict[str, Any]) -> None:
            raise ValueError("before_launch boom")

        def on_modify_command(
            self, ctx: ModuleContext, version_id: str, command: list[str], options: dict[str, Any]
        ) -> list[str]:
            raise ValueError("modify_command boom")

    rec = launcher.modules.register(CrashingHookModule(), source="external", ctx=ctx)
    header = launcher.modules.collect_header_lines(ctx)
    assert any("NullLauncher" in line for line in header)
    assert "ValueError" in rec.error

    cmd = launcher.modules.run_modify_command(ctx, "1.21.4", ["java", "-jar", "mc.jar"], {})
    assert cmd == ["java", "-jar", "mc.jar"]


def test_disabling_optional_module_removes_it_from_menu(tmp_path):
    ext_dir = tmp_path / "modules"
    ext_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OFFICIAL_MODULES_DIR / "news.py", ext_dir / "news.py")

    launcher = _make_launcher(tmp_path)
    ctx = launcher.module_context()

    news_rec = next(r for r in launcher.modules.records if r.id == "news")
    _, bottom_item = launcher.modules.build_main_menu(ctx)
    assert bottom_item is not None and bottom_item.value == "news"

    launcher.modules.set_record_enabled(news_rec, False, ctx)
    _, bottom_item = launcher.modules.build_main_menu(ctx)
    assert bottom_item is None

    # Core modules (settings & modules manager) cannot be disabled
    settings_rec = next(r for r in launcher.modules.records if r.id == "settings")
    launcher.modules.set_record_enabled(settings_rec, False, ctx)
    assert settings_rec.active is True


def test_module_settings_contributions_work(tmp_path):
    launcher = _make_launcher(tmp_path)
    ctx = launcher.module_context()
    triggered = []

    class ExtraSettingsModule(LauncherModule):
        meta = ModuleMetadata(id="extra_cfg", name="Extra Cfg")

        def get_settings_items(self, ctx: ModuleContext) -> list[SettingContribution]:
            return [
                SettingContribution(
                    id="toggle_extra",
                    label=tr("modules"),
                    handler=lambda _ctx: triggered.append(True),
                )
            ]

    launcher.modules.register(ExtraSettingsModule(), source="external", ctx=ctx)
    launcher.menu = DummyMenu([("module_setting", "toggle_extra"), "back"])
    launcher.settings_screen()
    assert triggered == [True]


def test_modules_package_and_official_modules_have_no_cyrillic_or_raw_ui_literals():
    root_dir = Path(__file__).resolve().parents[1]
    scan_files = (
        list((root_dir / "null_launcher" / "modules").glob("*.py"))
        + list(OFFICIAL_MODULES_DIR.glob("*.py"))
        + list(COMMUNITY_MODULES_DIR.glob("*.py"))
    )
    allowed_literals = {"", "1080", "SOCKS4", "SOCKS5", "-"}
    targets = {"prompt", "message", "confirm", "progress_task", "crash_screen"}
    for py_file in scan_files:
        if py_file.name == "registry.py":
            # registry.py contains MODULES_README documentation snippet
            continue
        source = py_file.read_text(encoding="utf-8")
        assert re.search(r"[А-Яа-яЁёІіЇїЄєЎў]", source) is None, py_file.name
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func_name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else (node.func.id if isinstance(node.func, ast.Name) else "")
            )
            if func_name in targets:
                for arg in node.args[:2]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        assert arg.value in allowed_literals, f"{py_file.name}:{node.lineno} {func_name}({arg.value!r})"
            if func_name == "MenuItem" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    assert arg.value in allowed_literals, f"{py_file.name}:{node.lineno} MenuItem({arg.value!r})"


def test_modrinth_detects_version_loader_and_mods_folder(tmp_path):
    ext_dir = tmp_path / "modules"
    ext_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(COMMUNITY_MODULES_DIR / "modrinth.py", ext_dir / "modrinth.py")

    launcher = _make_launcher(tmp_path)
    modrinth_mod = launcher.modules.get_active_module("modrinth")
    assert modrinth_mod is not None
    detect_version_context = sys_mod_func(modrinth_mod, "detect_version_context")

    launcher.store.settings["minecraft_dir"] = str(tmp_path / "mc")
    launcher.store.data["selected_version"] = "fabric-loader-0.16.9-1.21.1"
    launcher.store.data["managed_versions"]["fabric:1.21.1"] = {
        "kind": "loader",
        "loader_id": "fabric",
        "mc_version": "1.21.1",
        "installed_id": "fabric-loader-0.16.9-1.21.1",
    }
    mc_ver, loader_id, mods_dir = detect_version_context(launcher.module_context())
    assert mc_ver == "1.21.1"
    assert loader_id == "fabric"
    assert mods_dir == tmp_path / "mc" / "mods"

    # When separate_game_dir is enabled for the version, mods_dir points to instances/<slug>/mods
    launcher.store.data["version_settings"]["fabric-loader-0.16.9-1.21.1"] = {"separate_game_dir": True}
    _, _, separate_mods_dir = detect_version_context(launcher.module_context())
    assert "instances" in str(separate_mods_dir)
    assert separate_mods_dir.name == "mods"


def sys_mod_func(instance: Any, name: str) -> Any:
    import sys

    py_mod = sys.modules[type(instance).__module__]
    return getattr(py_mod, name)


def test_modrinth_download_and_dependency_installation(tmp_path, monkeypatch):
    import sys

    ext_dir = tmp_path / "modules"
    ext_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(COMMUNITY_MODULES_DIR / "modrinth.py", ext_dir / "modrinth.py")

    launcher = _make_launcher(tmp_path)
    ctx = launcher.module_context()
    mod = launcher.modules.get_active_module("modrinth")
    assert mod is not None
    modrinth_py_mod = sys.modules[type(mod).__module__]
    mods_dir = tmp_path / "mc" / "mods"

    main_jar_bytes = b"PK\x03\x04-fake-sodium-jar"
    dep_jar_bytes = b"PK\x03\x04-fake-fabric-api-jar"
    main_sha512 = hashlib.sha512(main_jar_bytes).hexdigest()
    dep_sha512 = hashlib.sha512(dep_jar_bytes).hexdigest()

    def fake_fetch_versions(project_id, *, loader="", mc_version=""):
        if project_id == "P7dR8mSH":
            return [
                {
                    "id": "dep-ver-1",
                    "files": [
                        {
                            "primary": True,
                            "filename": "fabric-api-0.100.0+1.21.1.jar",
                            "url": "https://cdn.modrinth.com/data/P7dR8mSH/versions/1/fabric-api.jar",
                            "size": len(dep_jar_bytes),
                            "hashes": {"sha512": dep_sha512},
                        }
                    ],
                }
            ]
        return []

    class FakeResp:
        def __init__(self, payload: bytes):
            self._payload = payload
            self._offset = 0
            self.headers = {"Content-Length": str(len(payload))}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size: int = -1) -> bytes:
            if self._offset >= len(self._payload):
                return b""
            chunk = self._payload[self._offset : self._offset + size]
            self._offset += len(chunk)
            return chunk

    def fake_urlopen(req, timeout=25.0):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "fabric-api" in url:
            return FakeResp(dep_jar_bytes)
        return FakeResp(main_jar_bytes)

    monkeypatch.setattr(modrinth_py_mod, "fetch_modrinth_versions", fake_fetch_versions)
    monkeypatch.setattr(modrinth_py_mod, "urlopen", fake_urlopen)

    version_obj = {
        "id": "sodium-ver-1",
        "dependencies": [{"project_id": "P7dR8mSH", "dependency_type": "required"}],
    }
    primary_file = {
        "primary": True,
        "filename": "sodium-fabric-0.6.0+mc1.21.1.jar",
        "url": "https://cdn.modrinth.com/data/AANobbMI/versions/1/sodium.jar",
        "size": len(main_jar_bytes),
        "hashes": {"sha512": main_sha512},
    }

    mod._install_mod_and_dependencies(
        ctx,
        project_id="AANobbMI",
        title="Sodium",
        version_obj=version_obj,
        primary_file=primary_file,
        loader="fabric",
        mc_version="1.21.1",
        mods_dir=mods_dir,
    )

    assert (mods_dir / "sodium-fabric-0.6.0+mc1.21.1.jar").read_bytes() == main_jar_bytes
    assert (mods_dir / "fabric-api-0.100.0+1.21.1.jar").read_bytes() == dep_jar_bytes
    installed_map = ctx.module_state("modrinth")["installed_projects"]
    assert installed_map["AANobbMI"] == "sodium-fabric-0.6.0+mc1.21.1.jar"
    assert installed_map["P7dR8mSH"] == "fabric-api-0.100.0+1.21.1.jar"
