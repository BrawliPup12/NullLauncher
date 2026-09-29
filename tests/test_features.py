from pathlib import Path

from null_launcher.crash_assistant import diagnose_minecraft_crash
from null_launcher.state import StateStore
from null_launcher import utils


def test_crash_assistant_detects_common_failures():
    hints = diagnose_minecraft_crash("java.lang.OutOfMemoryError: Java heap space\nMixinApplyError")
    assert [item.code for item in hints] == ["memory", "mods"]
    assert diagnose_minecraft_crash("normal shutdown") == []


def test_quick_play_state_keeps_three_unique_favorites(tmp_path):
    store = StateStore(tmp_path)
    for version in ("1.20.1", "1.21.1", "fabric-1.21.1", "1.21.8"):
        store.toggle_favorite_version(version)
    assert store.favorite_versions() == ["1.21.8", "fabric-1.21.1", "1.21.1"]
    store.record_launch("1.21.8", "Steve")
    assert store.last_launch()["version"] == "1.21.8"
    assert store.last_launch()["account"] == "Steve"


def test_portable_flag_moves_launcher_data_next_to_launcher(tmp_path, monkeypatch):
    (tmp_path / "portable.flag").write_text("", encoding="utf-8")
    monkeypatch.setattr(utils, "launcher_home", lambda: tmp_path)
    assert utils.portable_mode_enabled() is True
    assert utils.app_data_dir() == tmp_path / "data"
