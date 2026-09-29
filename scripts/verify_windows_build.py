from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from null_launcher.config import APP_VERSION  # noqa: E402


class VS_FIXEDFILEINFO(ctypes.Structure):
    _fields_ = [
        ("dwSignature", wintypes.DWORD),
        ("dwStrucVersion", wintypes.DWORD),
        ("dwFileVersionMS", wintypes.DWORD),
        ("dwFileVersionLS", wintypes.DWORD),
        ("dwProductVersionMS", wintypes.DWORD),
        ("dwProductVersionLS", wintypes.DWORD),
        ("dwFileFlagsMask", wintypes.DWORD),
        ("dwFileFlags", wintypes.DWORD),
        ("dwFileOS", wintypes.DWORD),
        ("dwFileType", wintypes.DWORD),
        ("dwFileSubtype", wintypes.DWORD),
        ("dwFileDateMS", wintypes.DWORD),
        ("dwFileDateLS", wintypes.DWORD),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", RGBQUAD * 1)]


def _version_tuple(value: str) -> tuple[int, int, int, int]:
    parts = [int(part) for part in value.split(".")]
    parts.extend([0] * (4 - len(parts)))
    return tuple(parts[:4])


def _exe_file_version(path: Path) -> tuple[int, int, int, int]:
    version = ctypes.windll.version
    dummy = wintypes.DWORD()
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    size = int(version.GetFileVersionInfoSizeW(str(path), ctypes.byref(dummy)))
    if size <= 0:
        raise RuntimeError("EXE has no Windows version resource")
    buf = ctypes.create_string_buffer(size)
    version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    version.GetFileVersionInfoW.restype = wintypes.BOOL
    if not version.GetFileVersionInfoW(str(path), 0, size, buf):
        raise ctypes.WinError()
    ptr = ctypes.c_void_p()
    length = wintypes.UINT()
    version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    version.VerQueryValueW.restype = wintypes.BOOL
    if not version.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
        raise ctypes.WinError()
    fixed = ctypes.cast(ptr, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
    if fixed.dwSignature != 0xFEEF04BD:
        raise RuntimeError("EXE version resource has an invalid signature")
    return (
        (fixed.dwFileVersionMS >> 16) & 0xFFFF,
        fixed.dwFileVersionMS & 0xFFFF,
        (fixed.dwFileVersionLS >> 16) & 0xFFFF,
        fixed.dwFileVersionLS & 0xFFFF,
    )


def _render_icon(hicon: int, size: int = 32) -> bytes:
    gdi32 = ctypes.windll.gdi32
    user32 = ctypes.windll.user32
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL
    user32.DrawIconEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HANDLE, ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.HBRUSH, wintypes.UINT]
    user32.DrawIconEx.restype = wintypes.BOOL
    hdc = gdi32.CreateCompatibleDC(None)
    if not hdc:
        raise ctypes.WinError()
    bits = ctypes.c_void_p()
    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = size
    info.bmiHeader.biHeight = -size
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0
    hbmp = gdi32.CreateDIBSection(hdc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
    if not hbmp or not bits.value:
        gdi32.DeleteDC(hdc)
        raise ctypes.WinError()
    old = gdi32.SelectObject(hdc, hbmp)
    try:
        ctypes.memset(bits, 0, size * size * 4)
        if not user32.DrawIconEx(hdc, 0, 0, hicon, size, size, 0, None, 0x0003):
            raise ctypes.WinError()
        return ctypes.string_at(bits, size * size * 4)
    finally:
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc)


def _verify_custom_icon(exe: Path, ico: Path) -> None:
    shell32 = ctypes.windll.shell32
    user32 = ctypes.windll.user32
    user32.DestroyIcon.argtypes = [wintypes.HANDLE]
    user32.DestroyIcon.restype = wintypes.BOOL
    large = ctypes.c_void_p()
    small = ctypes.c_void_p()
    shell32.ExtractIconExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p), wintypes.UINT]
    shell32.ExtractIconExW.restype = wintypes.UINT
    count = int(shell32.ExtractIconExW(str(exe), 0, ctypes.byref(large), ctypes.byref(small), 1))
    if count < 1 or not large.value:
        raise RuntimeError("EXE does not contain an extractable icon")

    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    source = user32.LoadImageW(None, str(ico), 1, 32, 32, 0x0010)
    if not source:
        raise RuntimeError("Could not load source NullLauncher.ico")
    try:
        exe_pixels = _render_icon(int(large.value), 32)
        source_pixels = _render_icon(int(source), 32)
        if exe_pixels != source_pixels:
            raise RuntimeError("EXE icon does not match assets/NullLauncher.ico")
    finally:
        if large.value:
            user32.DestroyIcon(large.value)
        if small.value:
            user32.DestroyIcon(small.value)
        user32.DestroyIcon(source)



def _pe_subsystem(path: Path) -> int:
    data = path.read_bytes()
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise RuntimeError("Build is not a valid PE executable")
    pe_offset = int.from_bytes(data[0x3C:0x40], "little")
    if pe_offset + 24 + 70 > len(data) or data[pe_offset:pe_offset + 4] != b"PE\x00\x00":
        raise RuntimeError("Build has an invalid PE header")
    optional = pe_offset + 24
    magic = int.from_bytes(data[optional:optional + 2], "little")
    if magic not in (0x10B, 0x20B):
        raise RuntimeError(f"Unsupported PE optional header magic: 0x{magic:04X}")
    return int.from_bytes(data[optional + 68:optional + 70], "little")


def _verify_windowed_subsystem(path: Path) -> None:
    subsystem = _pe_subsystem(path)
    if subsystem != 2:
        raise RuntimeError(f"EXE is not a Windows GUI application (PE subsystem={subsystem})")

def main() -> int:
    if os.name != "nt":
        print("Windows build verification skipped: not running on Windows")
        return 0
    exe = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "dist" / "NullLauncher.exe").resolve()
    ico = ROOT / "assets" / "NullLauncher.ico"
    if not exe.is_file():
        raise SystemExit(f"Build not found: {exe}")
    actual_version = _exe_file_version(exe)
    expected_version = _version_tuple(APP_VERSION)
    if actual_version != expected_version:
        raise SystemExit(f"Wrong EXE version resource: {actual_version}, expected {expected_version}")
    _verify_custom_icon(exe, ico)
    _verify_windowed_subsystem(exe)
    print(f"Verified Windows GUI metadata and custom icon: {exe.name} v{APP_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
