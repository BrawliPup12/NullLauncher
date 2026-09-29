from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Callable, Optional


class DownloadCache:
    def __init__(self, root: Path, *, max_entries: int = 96, max_age_seconds: int = 30 * 24 * 60 * 60):
        self.root = Path(root)
        self.max_entries = max(8, int(max_entries))
        self.max_age_seconds = max(3600, int(max_age_seconds))

    def _key(self, url: str) -> str:
        return hashlib.sha256(str(url).encode("utf-8", errors="replace")).hexdigest()

    def _paths(self, url: str) -> tuple[Path, Path]:
        key = self._key(url)
        return self.root / f"{key}.bin", self.root / f"{key}.json"

    def get(self, url: str, *, max_age_seconds: Optional[int] = None) -> Optional[bytes]:
        data_path, meta_path = self._paths(url)
        try:
            if not data_path.exists() or data_path.stat().st_size <= 0:
                return None
            age_limit = self.max_age_seconds if max_age_seconds is None else max(0, int(max_age_seconds))
            if age_limit and time.time() - data_path.stat().st_mtime > age_limit:
                return None
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if str(meta.get("url") or "") != str(url):
                    return None
                expected = str(meta.get("sha256") or "")
                raw = data_path.read_bytes()
                if expected and hashlib.sha256(raw).hexdigest() != expected:
                    return None
                os.utime(data_path, None)
                return raw
            raw = data_path.read_bytes()
            os.utime(data_path, None)
            return raw
        except Exception:
            self.remove(url)
            return None

    def put(self, url: str, raw: bytes) -> bytes:
        self.root.mkdir(parents=True, exist_ok=True)
        data_path, meta_path = self._paths(url)
        tmp = data_path.with_suffix(".tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, data_path)
        meta = {
            "url": str(url),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
            "saved_at": int(time.time()),
        }
        meta_tmp = meta_path.with_suffix(".tmp")
        meta_tmp.write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(meta_tmp, meta_path)
        self.cleanup()
        return raw

    def fetch(self, url: str, loader: Callable[[], bytes], *, max_age_seconds: Optional[int] = None) -> bytes:
        cached = self.get(url, max_age_seconds=max_age_seconds)
        if cached is not None:
            return cached
        raw = loader()
        if not isinstance(raw, (bytes, bytearray)) or not raw:
            raise RuntimeError("Download returned no data")
        return self.put(url, bytes(raw))

    def remove(self, url: str) -> None:
        for path in self._paths(url):
            with contextlib.suppress(OSError):
                path.unlink()

    def clear(self) -> None:
        if not self.root.exists():
            return
        for path in self.root.iterdir():
            if path.is_file():
                with contextlib.suppress(OSError):
                    path.unlink()

    def cleanup(self) -> None:
        if not self.root.exists():
            return
        now = time.time()
        data_files = sorted(self.root.glob("*.bin"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        for index, path in enumerate(data_files):
            remove = index >= self.max_entries
            if not remove:
                with contextlib.suppress(OSError):
                    remove = now - path.stat().st_mtime > self.max_age_seconds
            if remove:
                key = path.stem
                with contextlib.suppress(OSError):
                    path.unlink()
                with contextlib.suppress(OSError):
                    (self.root / f"{key}.json").unlink()
