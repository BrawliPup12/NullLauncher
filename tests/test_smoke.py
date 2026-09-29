from null_launcher.config import APP_NAME, APP_VERSION
from null_launcher.terminal import centered_sixel_column
from null_launcher.updater import _validate_update_payload, UpdateInfo


def test_identity():
    assert APP_NAME == "NullLauncher"
    assert APP_VERSION == "1.11.3"


def test_centered_sixel_column_is_symmetric():
    total = 111
    occupied = 47
    col = centered_sixel_column(total, occupied)
    left = col - 1
    right = total - (left + occupied)
    assert abs(left - right) <= 1


def test_exe_update_payload_validation():
    info = UpdateInfo(
        version="99.0.0",
        tag="v99.0.0",
        release_url="https://example.invalid/release",
        download_url="https://example.invalid/NullLauncher.exe",
    )
    assert _validate_update_payload(b"MZ" + b"x" * 1024, info) == "99.0.0"


def test_da1_sixel_detection():
    from null_launcher.terminal import Terminal

    assert Terminal._da1_reports_sixel("\x1b[?61;4;6;7;14c") is True
    assert Terminal._da1_reports_sixel("[?61;4;6;7;14c") is True
    assert Terminal._da1_reports_sixel("\x1b[?1;0c") is False
    assert Terminal._da1_reports_sixel("garbage") is None


def test_versions_screen_builds_version_rows_without_name_error():
    from null_launcher.app import NullLauncher
    from null_launcher.catalog import VersionEntry

    class DummyData:
        def build_catalog(self, filter_kind, query):
            return [
                VersionEntry(
                    key="vanilla:1.21.8",
                    label="1.21.8",
                    kind="vanilla",
                    mc_version="1.21.8",
                    version_type="release",
                    installed=False,
                )
            ]

    class DummyMenu:
        def __init__(self):
            self.items = None

        def choose(self, title, items, **kwargs):
            self.items = items
            return ("back", None)

    launcher = object.__new__(NullLauncher)
    launcher.data = DummyData()
    launcher.menu = DummyMenu()
    launcher.versions_screen()

    assert launcher.menu.items is not None
    assert any(getattr(item, "hint", None) == "release" for item in launcher.menu.items)


def test_launch_flow_has_no_hardcoded_cyrillic_ui_text():
    import inspect
    import re
    from null_launcher.app import NullLauncher

    source = inspect.getsource(NullLauncher.play) + inspect.getsource(NullLauncher._ensure_legacy_java)
    assert re.search(r"[А-Яа-яЁё]", source) is None


def test_launch_text_exists_for_every_language():
    from null_launcher.config import I18N, LANGUAGES

    keys = {
        "install_java_runtime", "no_account_title", "no_account_body",
        "no_version_title", "no_version_body", "version_missing_title",
        "version_missing_body", "verify_version", "empty_launch_command",
        "minecraft_running", "player_label", "proxy_label", "log_label",
        "launcher_returns", "game_failed_title", "exit_code",
        "last_log_lines", "empty_log", "launch_error_title", "see_launcher_log",
    }
    for language in LANGUAGES:
        assert keys <= I18N[language].keys()


def test_news_filter_color_is_independent_from_primary_theme():
    from PIL import Image
    from null_launcher import config
    from null_launcher.utils import _cmd_filter_image

    settings = {
        "language": "en",
        "theme_primary": "#FF0000",
        "theme_subtitle": "#9AA4AD",
        "theme_status": "#737D86",
        "news_filter_color": "#00FF00",
    }
    config.apply_runtime_preferences(settings)
    image = Image.new("RGB", (64, 48), "white")
    filtered = _cmd_filter_image(image, 64, 48)
    r, g, b = filtered.getpixel((10, 10))
    assert g > r and g > b
    config.apply_runtime_preferences({
        "language": "en",
        "theme_primary": config.DEFAULT_THEME["theme_primary"],
        "theme_subtitle": config.DEFAULT_THEME["theme_subtitle"],
        "theme_status": config.DEFAULT_THEME["theme_status"],
        "news_filter_color": config.DEFAULT_THEME["news_filter_color"],
    })


def test_news_filter_label_exists_for_every_language():
    from null_launcher.config import I18N, LANGUAGES

    for language in LANGUAGES:
        assert I18N[language].get("news_filter_color")


def test_update_installer_replaces_target_and_keeps_backup(tmp_path):
    import hashlib
    from null_launcher.updater import _install_downloaded_executable

    source = tmp_path / "downloaded.exe"
    target = tmp_path / "NullLauncher.exe"
    source.write_bytes(b"MZ-new-launcher")
    target.write_bytes(b"MZ-old-launcher")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()

    backup, staged = _install_downloaded_executable(source, target, expected)
    assert target.read_bytes() == b"MZ-new-launcher"
    assert backup.read_bytes() == b"MZ-old-launcher"
    assert not staged.exists()


def test_windows_updater_uses_trusted_copy_as_helper():
    import inspect
    from null_launcher import updater

    source = inspect.getsource(updater.spawn_update_replacer)
    assert "_copy_running_helper" in source
    assert "--update-source" in source
    assert "--apply-update" in source
    assert "CREATE_NO_WINDOW" in source
    assert "_independent_frozen_env" in source
    assert "powershell" not in source.lower()


def test_terminal_text_input_preserves_letter_case():
    import inspect
    from null_launcher.terminal import Terminal

    win_source = inspect.getsource(Terminal._read_windows_event)
    posix_source = inspect.getsource(Terminal._read_posix_event)
    assert "ch.lower()" not in win_source
    assert "ch.lower()" not in posix_source


def test_cli_accepts_internal_update_helper_arguments():
    from null_launcher.cli import parse_args

    args = parse_args([
        "--apply-update", "C:/Apps/NullLauncher.exe",
        "--wait-pid", "123",
        "--expected-sha256", "ab" * 32,
        "--update-source", "C:/Temp/new.exe",
        "--update-metadata", "C:/Temp/meta.json",
        "--update-log", "C:/Temp/update.log",
    ])
    assert args.apply_update.endswith("NullLauncher.exe")
    assert args.wait_pid == 123
    assert args.expected_sha256 == "ab" * 32
    assert args.update_source.endswith("new.exe")


def test_update_staging_directory_is_separate_and_overridable(tmp_path, monkeypatch):
    from null_launcher.updater import _update_root

    staging = tmp_path / "private-updates"
    monkeypatch.setenv("NULLLAUNCHER_UPDATE_DIR", str(staging))
    assert _update_root() == staging.resolve()


def test_new_ux_text_exists_for_every_language():
    from null_launcher.config import I18N, LANGUAGES

    keys = {
        "already_running", "first_run_title", "first_run_language",
        "first_run_minecraft_dir", "first_run_ram_min", "first_run_ram_max",
        "first_run_account", "first_run_done_title", "launching_title",
        "launch_stage_verify", "launcher_crash_title", "game_crash_title",
        "clear_download_cache", "whats_new_title", "whats_new_continue",
    }
    for language in LANGUAGES:
        assert keys <= I18N[language].keys()


def test_existing_state_does_not_trigger_first_run(tmp_path):
    import json
    from null_launcher.state import StateStore

    old = {
        "schema": 4,
        "accounts": [{"name": "Steve", "uuid": "ignored", "type": "offline"}],
        "settings": {"language": "en"},
    }
    (tmp_path / "state.json").write_text(json.dumps(old), encoding="utf-8")
    store = StateStore(tmp_path)
    assert store.settings["first_run_complete"] is True


def test_fresh_state_requests_first_run(tmp_path):
    from null_launcher.state import StateStore

    store = StateStore(tmp_path)
    assert store.settings["first_run_complete"] is False


def test_download_cache_roundtrip_and_clear(tmp_path):
    from null_launcher.download_cache import DownloadCache

    cache = DownloadCache(tmp_path / "downloads")
    calls = []
    data1 = cache.fetch("https://example.invalid/file.bin", lambda: calls.append(1) or b"abc")
    data2 = cache.fetch("https://example.invalid/file.bin", lambda: calls.append(2) or b"def")
    assert data1 == b"abc"
    assert data2 == b"abc"
    assert calls == [1]
    cache.clear()
    assert cache.get("https://example.invalid/file.bin") is None


def test_update_info_keeps_release_notes():
    from null_launcher.updater import UpdateInfo

    info = UpdateInfo(
        version="9.9.9",
        tag="v9.9.9",
        release_url="https://example.invalid/release",
        download_url="https://example.invalid/NullLauncher.exe",
        notes="Added safer updates",
    )
    assert "safer updates" in info.notes


def test_single_instance_guard_blocks_second_instance():
    from null_launcher.instance import SingleInstanceGuard

    first = SingleInstanceGuard("NullLauncher-test-guard")
    second = SingleInstanceGuard("NullLauncher-test-guard")
    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()
        second.release()


def test_every_language_has_complete_ui_catalog_and_splash():
    from null_launcher.config import I18N, LANGUAGES, SPLASH_LINES

    expected = set(I18N["en"])
    for language in LANGUAGES:
        assert set(I18N[language]) == expected
        assert len(SPLASH_LINES.get(language, [])) >= 3


def test_main_ui_has_no_hardcoded_cyrillic_outside_i18n():
    from pathlib import Path
    import re

    project = Path(__file__).resolve().parents[1] / "null_launcher"
    for name in ("app.py", "catalog.py", "cli.py", "minecraft.py", "terminal.py", "updater.py"):
        source = (project / name).read_text(encoding="utf-8")
        assert re.search(r"[А-Яа-яЁёІіЇїЄєЎў]", source) is None, name


def test_interactive_ui_calls_do_not_use_literal_labels():
    from pathlib import Path
    import ast

    source_path = Path(__file__).resolve().parents[1] / "null_launcher" / "app.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed_literals = {"", "1080", "SOCKS4", "SOCKS5"}
    targets = {"prompt", "message", "confirm", "progress_task", "crash_screen"}
    failures = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            name = node.func.attr
        elif isinstance(node.func, ast.Name):
            name = node.func.id
        else:
            name = ""
        if name in targets:
            for arg in node.args[:2]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value not in allowed_literals:
                    failures.append((node.lineno, name, arg.value))
        if name == "MenuItem" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value not in allowed_literals:
                failures.append((node.lineno, name, arg.value))
    assert not failures


def test_windows_build_has_icon_and_version_metadata():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = (root / "NullLauncher.spec").read_text(encoding="utf-8")
    version_info = (root / "assets" / "version_info.txt").read_text(encoding="utf-8")
    assert (root / "assets" / "NullLauncher.ico").is_file()
    assert 'ICON = ROOT / "assets" / "NullLauncher.ico"' in spec
    assert 'VERSION_INFO = ROOT / "assets" / "version_info.txt"' in spec
    assert 'icon=str(ICON)' in spec
    assert 'version=str(VERSION_INFO)' in spec
    assert "StringStruct('ProductName', 'NullLauncher')" in version_info
    assert "StringStruct('FileDescription', 'Terminal Minecraft Launcher')" in version_info
    assert "StringStruct('CompanyName', 'BrawliPup12')" in version_info
    assert "StringStruct('FileVersion', '1.11.3.0')" in version_info
    assert "StringStruct('ProductVersion', '1.11.3.0')" in version_info


def test_console_window_icon_is_applied_on_windows():
    import inspect
    from null_launcher.terminal import Terminal

    source = inspect.getsource(Terminal._apply_window_icon)
    assert "GetConsoleWindow" in source
    assert "LoadImageW" in source
    assert "SendMessageW" in source
    assert "NullLauncher.ico" in source


def test_failed_update_marker_prevents_immediate_auto_retry(tmp_path, monkeypatch):
    from null_launcher import updater

    monkeypatch.setenv("NULLLAUNCHER_UPDATE_DIR", str(tmp_path / "updates"))
    updater._record_failed_update("9.9.9", "health check failed", "ab" * 32)
    assert updater._recent_failed_update("9.9.9") is True
    assert updater._recent_failed_update("9.9.8") is False
    updater._clear_failed_update("9.9.9")
    assert updater._recent_failed_update("9.9.9") is False


def test_post_update_launch_can_skip_one_auto_update(monkeypatch, tmp_path):
    from null_launcher.cli import parse_args

    args = parse_args(["--skip-update-once"])
    assert args.skip_update_once is True


def test_build_pipeline_verifies_custom_icon_resource():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    build = (root / "scripts" / "build.ps1").read_text(encoding="utf-8")
    workflow = (root / ".github" / "workflows" / "build-windows.yml").read_text(encoding="utf-8")
    verifier = (root / "scripts" / "verify_windows_build.py").read_text(encoding="utf-8")
    assert "verify_windows_build.py" in build
    assert "verify_windows_build.py" in workflow
    assert "Verify release tag matches launcher version" in workflow
    assert "EXE icon does not match assets/NullLauncher.ico" in verifier


def test_console_icon_prefers_embedded_executable_icon():
    import inspect
    from null_launcher.terminal import Terminal

    source = inspect.getsource(Terminal._apply_window_icon)
    assert "ExtractIconExW" in source
    assert "SetCurrentProcessExplicitAppUserModelID" in source
    assert "SHChangeNotify" in source


def test_frozen_restarts_reset_pyinstaller_environment():
    import inspect
    from null_launcher import updater

    source = inspect.getsource(updater._independent_frozen_env)
    assert "PYINSTALLER_RESET_ENVIRONMENT" in source
    assert inspect.getsource(updater._launch_visible_launcher).count("_independent_frozen_env") == 1
