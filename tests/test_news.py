import struct
import zlib

import pytest

from null_launcher.news import _validate_news_asset_url, pretranslate_news_catalog
from null_launcher.utils import decode_article_image


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def test_news_assets_require_public_https():
    for url in (
        "http://example.com/a.png",
        "https://localhost/a.png",
        "https://127.0.0.1/a.png",
        "https://10.0.0.1/a.png",
        "https://[::1]/a.png",
    ):
        with pytest.raises(RuntimeError):
            _validate_news_asset_url(url, resolve_host=False)
    assert _validate_news_asset_url("https://8.8.8.8/a.png", resolve_host=False).startswith("https://")


def test_article_image_rejects_huge_dimensions_before_decode(monkeypatch):
    from PIL import Image

    class HugeImage:
        size = (20_000, 20_000)
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def load(self): raise AssertionError("load() must not run for oversized images")

    monkeypatch.setattr(Image, "open", lambda *args, **kwargs: HugeImage())
    with pytest.raises(ValueError, match="dimensions|pixels"):
        decode_article_image(b"fake")


def test_news_pretranslation_warms_only_requested_language(monkeypatch):
    calls = []

    def fake(entries, language, cache):
        calls.append(language)
        return {"id": {"signature": "s", "title": "x", "description": "y", "category": "z"}}

    monkeypatch.setattr("null_launcher.news._translate_news_language_batches", fake)
    result = pretranslate_news_catalog([{"title": "Hello", "url": "https://example.com/a"}], {}, "ru")
    assert calls == ["ru"]
    assert set(result) == {"ru"}
