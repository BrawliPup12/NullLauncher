from __future__ import annotations

import contextlib
import ctypes
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

from .config import APP_NAME, APP_VERSION, UPDATE_API_LATEST, UPDATE_MAX_BYTES, UPDATE_REPO_URL, UPDATE_SIGNER_SUBJECT, UPDATE_SIGNER_THUMBPRINT
from .download_cache import DownloadCache
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
    notes: str = ""


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
    now = time.time()
    for path in root.iterdir():
        if path.name in {"pending-update.json", "failed-update.json", "cache"}:
            continue
        if path.name == "update.log":
            continue
        try:
            if path.is_file() and now - path.stat().st_mtime > max_age_seconds:
                path.unlink()
        except OSError:
            pass
    DownloadCache(root / "cache", max_entries=4, max_age_seconds=14 * 24 * 60 * 60).cleanup()


FAILED_UPDATE_RETRY_SECONDS = 24 * 60 * 60


def _failed_update_path() -> Path:
    return _update_root() / "failed-update.json"


def _read_failed_update() -> Optional[dict[str, Any]]:
    path = _failed_update_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        with contextlib.suppress(OSError):
            path.unlink()
        return None
    return payload if isinstance(payload, dict) else None


def _record_failed_update(version: str, reason: str, digest: str = "") -> None:
    root = _prepare_update_root()
    path = root / "failed-update.json"
    payload = {
        "version": _release_version(version),
        "digest": str(digest or "").strip().lower(),
        "reason": str(reason or "")[:1000],
        "failed_at": int(time.time()),
    }
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        _mark_hidden(path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()


def _clear_failed_update(version: str = "") -> None:
    path = _failed_update_path()
    if not path.exists():
        return
    if version:
        payload = _read_failed_update()
        if payload is not None and _release_version(payload.get("version")) != _release_version(version):
            return
    with contextlib.suppress(OSError):
        path.unlink()


def _recent_failed_update(version: str, *, max_age_seconds: int = FAILED_UPDATE_RETRY_SECONDS) -> bool:
    payload = _read_failed_update()
    if not payload or _release_version(payload.get("version")) != _release_version(version):
        return False
    try:
        failed_at = int(payload.get("failed_at") or 0)
    except (TypeError, ValueError):
        return False
    if failed_at <= 0:
        return False
    if time.time() - failed_at < max(60, int(max_age_seconds)):
        return True
    _clear_failed_update(version)
    return False


def _normalize_sha256_digest(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw.startswith("sha256:"):
        raw = raw.split(":", 1)[1].strip()
    return f"sha256:{raw}" if re.fullmatch(r"[0-9a-f]{64}", raw) else ""


def _checksum_asset_digest(assets: list[Any], timeout: float) -> str:
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "").strip().lower()
        if name not in {"nulllauncher.exe.sha256", "nulllauncher.sha256", "sha256sums.txt"}:
            continue
        url = str(asset.get("browser_download_url") or "")
        if not url.startswith("https://"):
            continue
        request = Request(url, headers=_github_headers(binary=True))
        with urlopen(request, timeout=timeout) as response:
            text = response.read(16 * 1024).decode("utf-8", errors="replace")
        if name == "sha256sums.txt":
            # A multi-file checksum manifest must name the launcher explicitly;
            # never accept an unrelated first hash from the file.
            match = re.search(
                r"(?im)^\s*([0-9a-f]{64})\s+[* ]?NullLauncher\.exe\s*$",
                text,
            )
        else:
            # Dedicated sidecars contain only the launcher checksum.
            match = re.search(r"(?i)\b([0-9a-f]{64})\b", text)
        if match:
            return f"sha256:{match.group(1).lower()}"
    return ""


def _verify_authenticode_signature(path: Path) -> None:
    """Enforce an exact Authenticode publisher identity when configured."""
    expected_subject = str(UPDATE_SIGNER_SUBJECT or "").strip()
    expected_thumbprint = re.sub(r"[^0-9A-Fa-f]", "", str(UPDATE_SIGNER_THUMBPRINT or "")).upper()
    if not expected_subject and not expected_thumbprint:
        return
    if os.name != "nt":
        raise RuntimeError("Authenticode verification is configured but Windows is unavailable")
    escaped = str(path.resolve()).replace("'", "''")
    script = (
        f"$s=Get-AuthenticodeSignature -LiteralPath '{escaped}'; "
        "$c=$s.SignerCertificate; "
        "[pscustomobject]@{Status=[string]$s.Status;Subject=if($c){[string]$c.Subject}else{''};"
        "Thumbprint=if($c){[string]$c.Thumbprint}else{''}} | ConvertTo-Json -Compress"
    )
    system_root = Path(os.environ.get("SystemRoot") or r"C:\Windows")
    powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not powershell.is_file():
        raise RuntimeError("Windows PowerShell is unavailable for Authenticode verification")
    proc = subprocess.run(
        [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=20, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        raise RuntimeError("Authenticode verification command failed")
    try:
        payload = json.loads(proc.stdout.strip())
    except Exception as exc:
        raise RuntimeError("Authenticode verification returned invalid data") from exc
    if str(payload.get("Status") or "").casefold() != "valid":
        raise RuntimeError("Update Authenticode signature is not valid")
    actual_subject = str(payload.get("Subject") or "").strip()
    actual_thumbprint = re.sub(r"[^0-9A-Fa-f]", "", str(payload.get("Thumbprint") or "")).upper()
    if expected_subject and actual_subject != expected_subject:
        raise RuntimeError("Update Authenticode publisher does not match the configured subject")
    if expected_thumbprint and actual_thumbprint != expected_thumbprint:
        raise RuntimeError("Update Authenticode certificate thumbprint does not match")


def check_github_update(timeout: float = 4.5, *, allow_failed_retry: bool = False) -> Optional[UpdateInfo]:
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
    if not allow_failed_retry and _recent_failed_update(version):
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

    digest = _normalize_sha256_digest(selected.get("digest"))
    if not digest:
        digest = _checksum_asset_digest(candidates, timeout)
    if not digest:
        raise RuntimeError(f"Release {tag or version} has no trusted SHA-256 for NullLauncher.exe")

    notes = str(payload.get("body") or "").replace("\r\n", "\n").strip()[:12000]
    return UpdateInfo(
        version=version,
        tag=tag or f"v{version}",
        release_url=release_url,
        download_url=str(selected["browser_download_url"]),
        digest=digest,
        source="asset",
        asset_name=str(selected.get("name") or "NullLauncher.exe"),
        notes=notes,
    )


def _validate_update_payload(raw: bytes, info: UpdateInfo) -> str:
    if not raw or len(raw) > UPDATE_MAX_BYTES:
        raise RuntimeError("Downloaded update has an invalid size")
    digest = _normalize_sha256_digest(info.digest)
    if not digest:
        raise RuntimeError("Update has no valid SHA-256 digest; refusing to install")
    expected = digest.split(":", 1)[1]
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        raise RuntimeError("GitHub release SHA-256 verification failed")
    if not raw.startswith(b"MZ"):
        raise RuntimeError("Downloaded update is not a Windows executable")
    if not _is_newer_version(info.version):
        raise RuntimeError("Downloaded launcher is not newer than the running version")
    return info.version


def _download_update_bytes(info: UpdateInfo, timeout: float) -> bytes:
    request = Request(info.download_url, headers=_github_headers(binary=True))
    with urlopen(request, timeout=timeout) as response:
        return response.read(UPDATE_MAX_BYTES + 1)


def download_github_update(info: UpdateInfo, target: Path, timeout: float = 30.0) -> Path:
    root = _prepare_update_root()
    cache = DownloadCache(root / "cache", max_entries=4, max_age_seconds=14 * 24 * 60 * 60)
    raw = cache.get(info.download_url, max_age_seconds=14 * 24 * 60 * 60)
    if raw is not None:
        try:
            _validate_update_payload(raw, info)
        except Exception:
            cache.remove(info.download_url)
            raw = None
    if raw is None:
        raw = _download_update_bytes(info, timeout)
        _validate_update_payload(raw, info)
        cache.put(info.download_url, raw)

    safe_version = re.sub(r"[^0-9A-Za-z._-]+", "-", info.version).strip("-") or "update"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{APP_NAME}-{safe_version}-", suffix=".exe", dir=str(root))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        _mark_hidden(tmp)
        _verify_authenticode_signature(tmp)
        return tmp
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise



def _append_update_log(path: Path, message: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except OSError:
        pass


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest().lower()


def _wait_for_pid_exit(pid: int, timeout: float = 120.0) -> bool:
    if pid <= 0:
        return True
    if os.name == "nt":
        SYNCHRONIZE = 0x00100000
        WAIT_OBJECT_0 = 0x00000000
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k32.WaitForSingleObject.restype = ctypes.c_uint32
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = k32.OpenProcess(SYNCHRONIZE, False, int(pid))
        if not handle:
            return True
        try:
            result = int(k32.WaitForSingleObject(handle, max(0, int(timeout * 1000))))
            return result == WAIT_OBJECT_0
        finally:
            k32.CloseHandle(handle)

    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        time.sleep(0.10)
    return False


def _schedule_delete_on_reboot(path: Path) -> None:
    if os.name != "nt":
        return
    with contextlib.suppress(Exception):
        MOVEFILE_DELAY_UNTIL_REBOOT = 0x00000004
        move_file_ex = ctypes.windll.kernel32.MoveFileExW
        move_file_ex.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        move_file_ex.restype = ctypes.c_int
        move_file_ex(str(path), None, MOVEFILE_DELAY_UNTIL_REBOOT)


def schedule_cleanup_path(path: str | Path) -> None:
    target = Path(path).expanduser()
    if not str(target):
        return

    def worker() -> None:
        for _ in range(160):
            time.sleep(0.25)
            try:
                target.unlink(missing_ok=True)
                return
            except OSError:
                continue
        _schedule_delete_on_reboot(target)

    threading.Thread(target=worker, name="NullUpdateCleanup", daemon=True).start()


def _independent_frozen_env() -> dict[str, str]:
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def _launch_visible_launcher(target: Path, extra_args: Optional[list[str]] = None) -> subprocess.Popen[Any]:
    args = [str(target)] + list(extra_args or [])
    flags = 0
    if os.name == "nt":
        flags |= getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen(
        args,
        cwd=str(target.parent),
        stdin=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
        env=_independent_frozen_env(),
    )


def _install_downloaded_executable(source: Path, target: Path, expected_hash: str) -> tuple[Path, Path]:
    target.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    staged = target.parent / f".{APP_NAME}.update-{token}.exe"
    backup = target.parent / f".{APP_NAME}.previous-{token}.exe"
    shutil.copyfile(source, staged)
    _mark_hidden(staged)
    actual = _sha256_path(staged)
    if actual != expected_hash.lower():
        staged.unlink(missing_ok=True)
        raise RuntimeError("Staged update hash mismatch")
    _verify_authenticode_signature(staged)

    moved_old = False
    try:
        if target.exists():
            os.replace(target, backup)
            moved_old = True
            _mark_hidden(backup)
        os.replace(staged, target)
        if _sha256_path(target) != expected_hash.lower():
            raise RuntimeError("Installed update hash mismatch")
        _verify_authenticode_signature(target)
        return backup, staged
    except Exception:
        with contextlib.suppress(OSError):
            staged.unlink()
        if moved_old and backup.exists():
            with contextlib.suppress(OSError):
                target.unlink()
            with contextlib.suppress(OSError):
                os.replace(backup, target)
        raise


def _read_update_version_from_metadata(metadata: Optional[Path]) -> str:
    if metadata is None or not metadata.exists():
        return ""
    try:
        payload = json.loads(metadata.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    return _release_version(payload.get("version"))


def run_update_helper(
    target: str | Path,
    wait_pid: int,
    expected_hash: str,
    update_source: str | Path = "",
    metadata_path: str | Path = "",
    log_path: str | Path = "",
) -> int:
    helper = Path(sys.executable).resolve()
    source = Path(update_source).expanduser().resolve() if str(update_source) else helper
    target_path = Path(target).expanduser().resolve()
    root = helper.parent
    log = Path(log_path).expanduser() if str(log_path) else (root.parent / "update.log")
    metadata = Path(metadata_path).expanduser() if str(metadata_path) else None
    pending = root / "pending-update.json"
    health = root / f".health-{uuid.uuid4().hex}"
    expected = str(expected_hash or "").strip().lower()
    update_version = _read_update_version_from_metadata(metadata)

    _append_update_log(log, f"Updater helper started from {helper}; payload is {source}.")
    if not source.is_file():
        reason = f"Update payload is missing: {source}"
        _append_update_log(log, reason)
        if update_version:
            _record_failed_update(update_version, reason, expected)
        _schedule_delete_on_reboot(helper)
        return 2
    try:
        source_hash = _sha256_path(source)
    except OSError as exc:
        reason = f"Could not read update payload: {type(exc).__name__}: {exc}"
        _append_update_log(log, reason)
        if update_version:
            _record_failed_update(update_version, reason, expected)
        _schedule_delete_on_reboot(helper)
        return 2
    if not expected or source_hash != expected:
        reason = "Updater payload hash mismatch; refusing to install."
        _append_update_log(log, reason)
        if update_version:
            _record_failed_update(update_version, reason, expected)
        if source != helper:
            with contextlib.suppress(OSError):
                source.unlink()
        _schedule_delete_on_reboot(helper)
        return 2

    if not _wait_for_pid_exit(int(wait_pid), 120.0):
        reason = f"Timed out waiting for launcher PID {wait_pid} to exit."
        _append_update_log(log, reason)
        if update_version:
            _record_failed_update(update_version, reason, expected)
        if source != helper:
            with contextlib.suppress(OSError):
                source.unlink()
        _schedule_delete_on_reboot(helper)
        return 3
    _append_update_log(log, "Previous launcher process exited.")

    backup: Optional[Path] = None
    installed = False
    last_error = ""
    for attempt in range(1, 81):
        try:
            backup, _ = _install_downloaded_executable(source, target_path, expected)
            installed = True
            _append_update_log(log, f"New executable installed on attempt {attempt}.")
            break
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            _append_update_log(log, f"Install attempt {attempt} failed: {last_error}")
            time.sleep(0.25)

    if not installed:
        reason = f"Update installation failed: {last_error or 'unknown error'}"
        _append_update_log(log, reason)
        if update_version:
            _record_failed_update(update_version, reason, expected)
        if target_path.exists():
            with contextlib.suppress(Exception):
                _launch_visible_launcher(target_path)
        if source != helper:
            with contextlib.suppress(OSError):
                source.unlink()
        _schedule_delete_on_reboot(helper)
        return 4

    if source != helper:
        with contextlib.suppress(OSError):
            source.unlink()

    with contextlib.suppress(OSError):
        health.unlink()

    if metadata is not None and metadata.exists():
        try:
            shutil.copyfile(metadata, pending)
            _mark_hidden(pending)
        except OSError as exc:
            _append_update_log(log, f"Could not stage What's New metadata: {exc}")

    proc: Optional[subprocess.Popen[Any]] = None
    try:
        proc = _launch_visible_launcher(
            target_path,
            ["--update-health-file", str(health), "--cleanup-update-helper", str(helper), "--skip-update-once"],
        )
        _append_update_log(log, f"Started updated launcher PID {proc.pid}; waiting for health signal.")
    except Exception as exc:
        _append_update_log(log, f"Could not restart updated launcher: {type(exc).__name__}: {exc}")

    healthy = False
    if proc is not None:
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            if health.exists():
                healthy = True
                break
            if proc.poll() is not None:
                break
            time.sleep(0.20)

    if healthy:
        _append_update_log(log, "Update health check passed.")
        if update_version:
            _clear_failed_update(update_version)
        if backup is not None:
            with contextlib.suppress(OSError):
                backup.unlink()
        if metadata is not None:
            with contextlib.suppress(OSError):
                metadata.unlink()
        with contextlib.suppress(OSError):
            health.unlink()
        _schedule_delete_on_reboot(helper)
        return 0

    reason = "Updated launcher did not pass health check; rolling back."
    _append_update_log(log, reason)
    if update_version:
        _record_failed_update(update_version, reason, expected)
    if proc is not None and proc.poll() is None:
        with contextlib.suppress(Exception):
            proc.terminate()
            proc.wait(timeout=5)
        if proc.poll() is None:
            with contextlib.suppress(Exception):
                proc.kill()
                proc.wait(timeout=5)

    with contextlib.suppress(OSError):
        pending.unlink()
    with contextlib.suppress(OSError):
        target_path.unlink()
    if backup is not None and backup.exists():
        try:
            os.replace(backup, target_path)
            _append_update_log(log, "Previous executable restored.")
        except OSError as exc:
            _append_update_log(log, f"Rollback restore failed: {exc}")
    if metadata is not None:
        with contextlib.suppress(OSError):
            metadata.unlink()
    with contextlib.suppress(OSError):
        health.unlink()

    if target_path.exists():
        try:
            _launch_visible_launcher(target_path, ["--skip-update-once"])
            _append_update_log(log, "Previous launcher restarted after rollback with one automatic retry suppressed.")
        except Exception as exc:
            _append_update_log(log, f"Rollback restart failed: {type(exc).__name__}: {exc}")
    _schedule_delete_on_reboot(helper)
    return 5


def _copy_running_helper(target: Path, root: Path) -> Path:
    token = uuid.uuid4().hex
    helper = root / f".{APP_NAME}-updater-{token}.exe"
    shutil.copy2(target, helper)
    _mark_hidden(helper)
    return helper


def spawn_update_replacer(downloaded: Path, target: Path, info: Optional[UpdateInfo] = None) -> None:
    downloaded = downloaded.resolve()
    target = target.resolve()

    if os.name == "nt" and getattr(sys, "frozen", False):
        root = _prepare_update_root()
        log_path = root.parent / "update.log"
        digest = _sha256_path(downloaded)
        token = uuid.uuid4().hex
        metadata = root / f".metadata-{token}.json"
        payload = {
            "version": str(info.version if info else APP_VERSION),
            "previous_version": APP_VERSION,
            "tag": str(info.tag if info else ""),
            "release_url": str(info.release_url if info else ""),
            "notes": str(info.notes if info else "")[:12000],
            "installed_at": int(time.time()),
        }
        metadata.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        _mark_hidden(metadata)

        helper: Optional[Path] = None
        try:
            helper = _copy_running_helper(target, root)
            _append_update_log(log_path, f"Launching trusted updater helper {helper.name} for {target}; payload is {downloaded.name}.")
            args = [
                str(helper),
                "--apply-update", str(target),
                "--update-source", str(downloaded),
                "--wait-pid", str(os.getpid()),
                "--expected-sha256", digest,
                "--update-metadata", str(metadata),
                "--update-log", str(log_path),
            ]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            with log_path.open("ab") as stream:
                proc = subprocess.Popen(
                    args,
                    cwd=str(root),
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    creationflags=flags,
                    close_fds=True,
                    env=_independent_frozen_env(),
                )
            _append_update_log(log_path, f"Updater helper process created with PID {proc.pid}.")
        except Exception as exc:
            _append_update_log(log_path, f"Failed to start updater helper: {type(exc).__name__}: {exc}")
            with contextlib.suppress(OSError):
                metadata.unlink()
            if helper is not None:
                with contextlib.suppress(OSError):
                    helper.unlink()
            raise
        return

    helper = """import os, subprocess, sys, time
src, target, python = sys.argv[1:4]
for _ in range(100):
    try:
        os.replace(src, target)
        subprocess.Popen([python, target], cwd=os.path.dirname(target) or None, close_fds=True)
        break
    except (PermissionError, OSError):
        time.sleep(0.10)
"""
    subprocess.Popen(
        [sys.executable, "-c", helper, str(downloaded), str(target), sys.executable],
        cwd=str(target.parent),
        close_fds=True,
    )

def clear_update_download_cache() -> None:
    DownloadCache(_prepare_update_root() / "cache", max_entries=4, max_age_seconds=14 * 24 * 60 * 60).clear()


def signal_update_health() -> None:
    raw = os.environ.get("NULLLAUNCHER_UPDATE_HEALTH_FILE", "").strip()
    if not raw:
        return
    path = Path(raw)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(f"{APP_VERSION}\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def consume_pending_update(current_version: str = APP_VERSION) -> Optional[dict[str, Any]]:
    path = _update_root() / "pending-update.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        with contextlib.suppress(OSError):
            path.unlink()
        return None
    if not isinstance(payload, dict):
        return None
    version = _release_version(payload.get("version"))
    if version != _release_version(current_version):
        if _version_tuple(version) < _version_tuple(current_version):
            with contextlib.suppress(OSError):
                path.unlink()
        return None
    with contextlib.suppress(OSError):
        path.unlink()
    return payload
