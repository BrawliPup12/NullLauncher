from __future__ import annotations

import hashlib
import hmac
from types import MappingProxyType
from typing import Callable


def _build_verifier() -> tuple[Callable[[str, bytes], tuple[bool, str]], MappingProxyType[str, str]]:
    # Pinned SHA-256 digests of normalized official NullLauncher external modules.
    # Stored inside a closure so external module code executing later cannot mutate it.
    _pinned: dict[str, str] = {
        "news": "98532f2e6ffbd5b2942f8eada1c372c24a5353dedf5a7096da0daaf6bd89a910",
        "proxies": "75f4c035f8f8e6937e31a9fcad364d9c18c375d741d12d3882a7e863377f8bcd",
    }
    _sha256 = hashlib.sha256
    _compare_digest = hmac.compare_digest

    def _verify(module_id: str, raw_bytes: bytes) -> tuple[bool, str]:
        if not isinstance(raw_bytes, (bytes, bytearray)):
            return False, ""
        normalized = bytes(raw_bytes).replace(b"\r\n", b"\n").strip() + b"\n"
        digest = _sha256(normalized).hexdigest()
        expected = _pinned.get(str(module_id).strip())
        if expected is not None and _compare_digest(digest, expected):
            return True, digest
        return False, digest

    return _verify, MappingProxyType(dict(_pinned))


verify_official_module_bytes, OFFICIAL_MODULE_DIGESTS = _build_verifier()
del _build_verifier
