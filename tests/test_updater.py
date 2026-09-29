import hashlib

import pytest

from null_launcher.updater import UpdateInfo, _checksum_asset_digest, _normalize_sha256_digest, _validate_update_payload


def _info(digest: str = "") -> UpdateInfo:
    return UpdateInfo(
        version="99.0.0",
        tag="v99.0.0",
        release_url="https://example.invalid/release",
        download_url="https://example.invalid/NullLauncher.exe",
        digest=digest,
    )


def test_update_fails_closed_without_sha256():
    with pytest.raises(RuntimeError, match="no valid SHA-256"):
        _validate_update_payload(b"MZpayload", _info())


def test_update_rejects_malformed_sha256():
    with pytest.raises(RuntimeError, match="no valid SHA-256"):
        _validate_update_payload(b"MZpayload", _info("sha256:1234"))


def test_update_requires_matching_sha256():
    raw = b"MZpayload"
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    assert _validate_update_payload(raw, _info(digest)) == "99.0.0"
    with pytest.raises(RuntimeError, match="verification failed"):
        _validate_update_payload(raw + b"tampered", _info(digest))


def test_sha256_normalizer_accepts_only_full_digest():
    digest = "ab" * 32
    assert _normalize_sha256_digest(digest) == "sha256:" + digest
    assert _normalize_sha256_digest("sha256:" + digest.upper()) == "sha256:" + digest
    assert _normalize_sha256_digest("sha256:" + digest[:-1]) == ""


def test_sha256s_manifest_must_name_launcher(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, _limit):
            return (
                ("11" * 32) + "  Other.exe\n" +
                ("22" * 32) + " *NullLauncher.exe\n"
            ).encode("ascii")

    monkeypatch.setattr("null_launcher.updater.urlopen", lambda *args, **kwargs: Response())
    assets = [{"name": "SHA256SUMS.txt", "browser_download_url": "https://example.invalid/SHA256SUMS.txt"}]
    assert _checksum_asset_digest(assets, 1.0) == "sha256:" + ("22" * 32)


def test_sha256s_manifest_rejects_unrelated_hash(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, _limit): return ((("33" * 32) + "  Other.exe\n").encode("ascii"))

    monkeypatch.setattr("null_launcher.updater.urlopen", lambda *args, **kwargs: Response())
    assets = [{"name": "SHA256SUMS.txt", "browser_download_url": "https://example.invalid/SHA256SUMS.txt"}]
    assert _checksum_asset_digest(assets, 1.0) == ""
