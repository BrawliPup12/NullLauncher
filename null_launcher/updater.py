from __future__ import annotations

import contextlib
import ctypes
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
import uuid

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


def _update_root() -> Path:
    override = os.environ.get("NULLLAUNCHER_UPDATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
        return Path(base) / APP_NAME / "updates"
    return Path(tempfile.gettempdir()) / APP_NAME / "updates"


def _mark_hidden(path: Path) -> None:
    if os.name != "nt":
        return
    with contextlib.suppress(Exception):
        get_attrs = ctypes.windll.kernel32.GetFileAttributesW
        set_attrs = ctypes.windll.kernel32.SetFileAttributesW
        get_attrs.argtypes = [ctypes.c_wchar_p]
        get_attrs.restype = ctypes.c_uint32
        set_attrs.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        set_attrs.restype = ctypes.c_int
        current = int(get_attrs(str(path)))
        if current != 0xFFFFFFFF:
            set_attrs(str(path), current | 0x2)


def _prepare_update_root() -> Path:
    root = _update_root()
    root.mkdir(parents=True, exist_ok=True)
    _mark_hidden(root)
    return root


def cleanup_update_artifacts(*, max_age_seconds: int = 48 * 60 * 60) -> None:
    root = _update_root()
    if not root.exists():
        return
    cutoff = time.time() - max(60, int(max_age_seconds))
    for path in root.iterdir():
        if not path.is_file():
            continue
        if path.name == "update.log":
            continue
        with contextlib.suppress(OSError):
            if path.stat().st_mtime < cutoff:
                path.unlink()


def check_github_update(timeout: float = 4.5) -> Optional[UpdateInfo]:
    if not getattr(sys, "frozen", False):
        return None

    cleanup_update_artifacts()
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
    request = Request(info.download_url, headers=_github_headers(binary=True))
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(UPDATE_MAX_BYTES + 1)
    _validate_update_payload(raw, info)

    root = _prepare_update_root()
    safe_version = re.sub(r"[^0-9A-Za-z._-]+", "-", info.version).strip("-") or "update"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{APP_NAME}-{safe_version}-",
        suffix=".exe",
        dir=str(root),
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        _mark_hidden(tmp)
        return tmp
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _build_windows_replacer_script(downloaded: Path, target: Path, pid: int, digest: str, log_path: Path) -> str:
    return (
        "$ErrorActionPreference = 'Stop'\n"
        f"$src = {_ps_quote(str(downloaded))}\n"
        f"$target = {_ps_quote(str(target))}\n"
        f"$pidToWait = {int(pid)}\n"
        f"$expectedHash = {_ps_quote(digest.upper())}\n"
        f"$logPath = {_ps_quote(str(log_path))}\n"
        "$targetDir = Split-Path -Parent $target\n"
        "$new = Join-Path $targetDir ('.NullLauncher.update-' + $pidToWait + '.exe')\n"
        "$backup = Join-Path $targetDir ('.NullLauncher.previous-' + $pidToWait + '.exe')\n"
        "$success = $false\n"
        "function Write-UpdateLog([string]$message) {\n"
        "  try { Add-Content -LiteralPath $logPath -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $message) -Encoding UTF8 } catch {}\n"
        "}\n"
        "function Hide-File([string]$path) {\n"
        "  try { if (Test-Path -LiteralPath $path) { (Get-Item -LiteralPath $path -Force).Attributes = ((Get-Item -LiteralPath $path -Force).Attributes -bor [IO.FileAttributes]::Hidden) } } catch {}\n"
        "}\n"
        "Write-UpdateLog 'Updater helper started.'\n"
        "try { Wait-Process -Id $pidToWait -ErrorAction SilentlyContinue } catch {}\n"
        "for ($attempt = 0; $attempt -lt 120 -and -not $success; $attempt++) {\n"
        "  try {\n"
        "    Remove-Item -LiteralPath $new -Force -ErrorAction SilentlyContinue\n"
        "    Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue\n"
        "    Copy-Item -LiteralPath $src -Destination $new -Force\n"
        "    Hide-File $new\n"
        "    $newHash = (Get-FileHash -LiteralPath $new -Algorithm SHA256).Hash.ToUpperInvariant()\n"
        "    if ($newHash -ne $expectedHash) { throw 'Staged update hash mismatch.' }\n"
        "    if (Test-Path -LiteralPath $target) {\n"
        "      try {\n"
        "        [System.IO.File]::Replace($new, $target, $backup, $true)\n"
        "      } catch {\n"
        "        Move-Item -LiteralPath $target -Destination $backup -Force\n"
        "        try { Move-Item -LiteralPath $new -Destination $target -Force }\n"
        "        catch { Move-Item -LiteralPath $backup -Destination $target -Force -ErrorAction SilentlyContinue; throw }\n"
        "      }\n"
        "    } else {\n"
        "      Move-Item -LiteralPath $new -Destination $target -Force\n"
        "    }\n"
        "    $targetHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToUpperInvariant()\n"
        "    if ($targetHash -ne $expectedHash) {\n"
        "      Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue\n"
        "      if (Test-Path -LiteralPath $backup) { Move-Item -LiteralPath $backup -Destination $target -Force }\n"
        "      throw 'Installed update hash mismatch.'\n"
        "    }\n"
        "    Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue\n"
        "    Remove-Item -LiteralPath $src -Force -ErrorAction SilentlyContinue\n"
        "    $success = $true\n"
        "    Write-UpdateLog 'Update installed successfully.'\n"
        "  } catch {\n"
        "    Write-UpdateLog ('Attempt ' + ($attempt + 1) + ' failed: ' + $_.Exception.Message)\n"
        "    if ((-not (Test-Path -LiteralPath $target)) -and (Test-Path -LiteralPath $backup)) { Move-Item -LiteralPath $backup -Destination $target -Force -ErrorAction SilentlyContinue }\n"
        "    Remove-Item -LiteralPath $new -Force -ErrorAction SilentlyContinue\n"
        "    Start-Sleep -Milliseconds 500\n"
        "  }\n"
        "}\n"
        "Remove-Item -LiteralPath $new -Force -ErrorAction SilentlyContinue\n"
        "Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue\n"
        "Remove-Item -LiteralPath $src -Force -ErrorAction SilentlyContinue\n"
        "if (Test-Path -LiteralPath $target) {\n"
        "  try { Start-Process -FilePath $target -WorkingDirectory $targetDir } catch { Write-UpdateLog ('Restart failed: ' + $_.Exception.Message) }\n"
        "}\n"
        "if (-not $success) { Write-UpdateLog 'Update failed; the previous launcher was restored when possible.' }\n"
        "Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue\n"
    )


def spawn_update_replacer(downloaded: Path, target: Path) -> None:
    downloaded = downloaded.resolve()
    target = target.resolve()

    if os.name == "nt" and getattr(sys, "frozen", False):
        root = _prepare_update_root()
        log_path = root.parent / "update.log"
        digest = hashlib.sha256(downloaded.read_bytes()).hexdigest()
        script = root / f".apply-{uuid.uuid4().hex}.ps1"
        script.write_text(
            _build_windows_replacer_script(downloaded, target, os.getpid(), digest, log_path),
            encoding="utf-8-sig",
        )
        _mark_hidden(script)
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        executable = str(powershell if powershell.exists() else "powershell.exe")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(
            [
                executable,
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ],
            cwd=str(root),
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
