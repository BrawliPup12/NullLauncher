from __future__ import annotations

import contextlib
import ctypes
import os
from pathlib import Path
import tempfile
from typing import Optional


class SingleInstanceGuard:
    def __init__(self, name: str):
        self.name = str(name or "NullLauncher")
        self._handle: Optional[int] = None
        self._lock_file = None

    def acquire(self) -> bool:
        if os.name == "nt":
            kernel32 = ctypes.windll.kernel32
            kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            handle = kernel32.CreateMutexW(None, 0, f"Local\\{self.name}-single-instance")
            if not handle:
                return True
            self._handle = int(handle)
            return int(kernel32.GetLastError()) != 183

        try:
            import fcntl

            path = Path(tempfile.gettempdir()) / f".{self.name.lower()}-instance.lock"
            handle = path.open("a+")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_file = handle
            return True
        except Exception:
            with contextlib.suppress(Exception):
                if self._lock_file:
                    self._lock_file.close()
            self._lock_file = None
            return False

    def release(self) -> None:
        if os.name == "nt" and self._handle:
            with contextlib.suppress(Exception):
                ctypes.windll.kernel32.ReleaseMutex(ctypes.c_void_p(self._handle))
            with contextlib.suppress(Exception):
                ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(self._handle))
            self._handle = None
            return
        if self._lock_file is not None:
            with contextlib.suppress(Exception):
                import fcntl
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            with contextlib.suppress(Exception):
                self._lock_file.close()
            self._lock_file = None

    def __enter__(self) -> "SingleInstanceGuard":
        if not self.acquire():
            raise RuntimeError("Another instance is already running")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def show_already_running(title: str, message: str) -> None:
    if os.name == "nt":
        with contextlib.suppress(Exception):
            ctypes.windll.user32.MessageBoxW(None, str(message), str(title), 0x40)
            return
    print(message)
