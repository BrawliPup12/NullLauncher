from null_launcher.config import APP_NAME, APP_VERSION
from null_launcher.terminal import centered_sixel_column
from null_launcher.updater import _validate_update_payload, UpdateInfo


def test_identity():
    assert APP_NAME == "NullLauncher"
    assert APP_VERSION == "1.10.3"


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
