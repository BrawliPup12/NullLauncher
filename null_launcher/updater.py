from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .config import (
    APP_NAME,
    APP_VERSION,
    UPDATE_API_LATEST,
    UPDATE_MAX_BYTES,
    UPDATE_REPO_URL,
)
from .utils import clean_markup


@dataclasses.dataclass(frozen=True)
class UpdateInfo:
    version: str
    tag: str
    release_url: str
    download_url: str
    digest: str = ""
    source: str = "asset"
    asset_name: str = "NullLauncher.exe"


def running_artifact_path() -> Path:
    """Path that can be atomically replaced by the updater.

    Release builds are PyInstaller one-file executables, so sys.executable is the
    launcher itself. Development/source mode is intentionally not auto-updated:
    a multi-module Git checkout should be updated with Git instead of replacing
    one Python file.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return (Path(__file__).resolve().parents[1] / "NullLauncher.py").resolve()


def _release_version(value: Any) -> str:
    raw = clean_markup(value).strip()
    raw = re.sub(r"^[vV]", "", raw)
    match = re.search(r"\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.-]+)?", raw)
    return match.group(0) if match else raw


def _version_tuple(value: Any) -> tuple[int, int, int, int]:
    core = _release_version(value).split("+", 1)[0].split("-", 1)[0]
    nums = [int(x) for x in re.findall(r"\d+", core)[:4]]
    nums.extend([0] * (4 - len(nums)))
    return tuple(nums[:4])                              


def _is_newer_version(candidate: Any, current: Any = APP_VERSION) -> bool:
    return _version_tuple(candidate) > _version_tuple(current)


def _github_headers(*, binary: bool = False) -> dict[str, str]:
    return {
        "Accept": "application/octet-stream" if binary else "application/vnd.github+json",
        "User-Agent": f"{APP_NAME}/{APP_VERSION} (+{UPDATE_REPO_URL})",
        "X-GitHub-Api-Version": "2026-03-10",
    }


def check_github_update(timeout: float = 4.5) -> Optional[UpdateInfo]:
    """Return a newer Windows EXE release for packaged builds.

    Source checkouts are not self-modified because NullLauncher is now a proper
    multi-file project. Developers update with Git; release users receive the EXE.
    """
    if not getattr(sys, "frozen", False):
        return None

    request = Request(UPDATE_API_LATEST, headers=_github_headers())
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8", errors="replace"))
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise

    if not isinstance(payload, dict):
        raise RuntimeError("GitHub returned an invalid release response")
    tag = clean_markup(payload.get("tag_name"))
    version = _release_version(tag or payload.get("name"))
    if not version or not _is_newer_version(version):
        return None

    assets = payload.get("assets") if isinstance(payload.get("assets"), list) else []
    candidates = [a for a in assets if isinstance(a, dict) and str(a.get("state") or "uploaded") == "uploaded"]
    selected: Optional[dict[str, Any]] = None
    for asset in candidates:
        if str(asset.get("name") or "").lower() == "nulllauncher.exe":
            selected = asset
            break
    if selected is None:
        for asset in candidates:
            name = str(asset.get("name") or "").lower()
            if name.startswith("nulllauncher") and name.endswith(".exe"):
                selected = asset
                break

    release_url = str(payload.get("html_url") or UPDATE_REPO_URL)
    if selected is None or not str(selected.get("browser_download_url") or "").startswith("https://"):
        raise RuntimeError(f"Release {tag or version} has no NullLauncher.exe asset")

    return UpdateInfo(
        version=version,
        tag=tag or f"v{version}",
        release_url=release_url,
        download_url=str(selected["browser_download_url"]),
        digest=str(selected.get("digest") or ""),
        source="asset",
        asset_name=str(selected.get("name") or "NullLauncher.exe"),
    )


def _validate_update_payload(raw: bytes, info: UpdateInfo) -> str:
    if not raw or len(raw) > UPDATE_MAX_BYTES:
        raise RuntimeError("Downloaded update has an invalid size")
    digest = str(info.digest or "").strip().lower()
    if digest.startswith("sha256:"):
        expected = digest.split(":", 1)[1].strip()
        actual = hashlib.sha256(raw).hexdigest()
        if not expected or actual != expected:
            raise RuntimeError("GitHub release SHA-256 verification failed")
    if not raw.startswith(b"MZ"):
        raise RuntimeError("Downloaded update is not a Windows executable")
    if not _is_newer_version(info.version):
        raise RuntimeError("Downloaded launcher is not newer than the running version")
    return info.version


def download_github_update(info: UpdateInfo, target: Path, timeout: float = 30.0) -> Path:
    """Download and validate an EXE next to the current launcher for replacement."""
    request = Request(info.download_url, headers=_github_headers(binary=True))
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(UPDATE_MAX_BYTES + 1)
    _validate_update_payload(raw, info)
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.stem}.update-", suffix=".exe", dir=str(target.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        return tmp
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def spawn_update_replacer(downloaded: Path, target: Path) -> None:
    """Replace the running EXE after it exits, then launch the new build."""
    downloaded = downloaded.resolve()
    target = target.resolve()

    if os.name == "nt" and getattr(sys, "frozen", False):
        fd, script_name = tempfile.mkstemp(prefix="nulllauncher-update-", suffix=".ps1")
        os.close(fd)
        script = Path(script_name)
        script.write_text(
            "$ErrorActionPreference = 'SilentlyContinue'\n"
            f"$src = {_ps_quote(str(downloaded))}\n"
            f"$target = {_ps_quote(str(target))}\n"
            f"$pidToWait = {os.getpid()}\n"
            "Wait-Process -Id $pidToWait -ErrorAction SilentlyContinue\n"
            "$backup = $target + '.bak'\n"
            "if (Test-Path -LiteralPath $target) { Copy-Item -LiteralPath $target -Destination $backup -Force }\n"
            "for ($i = 0; $i -lt 80; $i++) {\n"
            "  try { Move-Item -LiteralPath $src -Destination $target -Force -ErrorAction Stop; break }\n"
            "  catch { Start-Sleep -Milliseconds 125 }\n"
            "}\n"
            "if (Test-Path -LiteralPath $target) { Start-Process -FilePath $target -WorkingDirectory (Split-Path -Parent $target) }\n"
            "Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue\n",
            encoding="utf-8-sig",
        )
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            cwd=str(target.parent),
            creationflags=flags,
            close_fds=True,
        )
        return

                                                                             
                                                  
    helper = r'''import os, subprocess, sys, time
src, target, python = sys.argv[1:4]
for _ in range(100):
    try:
        os.replace(src, target)
        subprocess.Popen([python, target], cwd=os.path.dirname(target) or None, close_fds=True)
        break
    except (PermissionError, OSError):
        time.sleep(0.10)
'''
    subprocess.Popen(
        [sys.executable, "-c", helper, str(downloaded), str(target), sys.executable],
        cwd=str(target.parent),
        close_fds=True,
    )
