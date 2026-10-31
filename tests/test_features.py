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


def test_download_cache_put_and_get(tmp_path):
    from null_launcher.download_cache import DownloadCache
    cache = DownloadCache(tmp_path)
    data = b"hello minecraft cache"
    url = "https://example.com/asset.png"
    cached = cache.put(url, data)
    assert cached == data
    assert cache.get(url) == data


def test_tail_text_with_cp1251_and_utf8(tmp_path):
    utf8_file = tmp_path / "utf8.log"
    utf8_file.write_text("строка 1\nстрока 2\nстрока 3", encoding="utf-8")
    assert utils.tail_text(utf8_file, 2) == "строка 2\nстрока 3"

    cp1251_file = tmp_path / "cp1251.log"
    cp1251_file.write_bytes("ошибка Windows cp1251\nвторая строка".encode("cp1251"))
    assert "ошибка" in utils.tail_text(cp1251_file, 2)


def test_single_instance_guard(tmp_path):
    from null_launcher.instance import SingleInstanceGuard
    guard1 = SingleInstanceGuard("NullLauncherTest")
    assert guard1.acquire() is True
    try:
        guard2 = SingleInstanceGuard("NullLauncherTest")
        assert guard2.acquire() is False
    finally:
        guard1.release()


def test_crash_assistant_extended_signatures():
    hints = diagnose_minecraft_crash("java.lang.StackOverflowError\nworld generation loop")
    assert any(h.code == "mods" for h in hints)
    hints2 = diagnose_minecraft_crash("Unsupported major.minor version 61.0")
    assert any(h.code == "java" for h in hints2)
