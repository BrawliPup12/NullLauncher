                                      
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

ROOT = Path(globals().get("SPECPATH", ".")).resolve()
ICON = ROOT / "assets" / "NullLauncher.ico"
VERSION_INFO = ROOT / "assets" / "version_info.txt"

mll_datas, mll_binaries, mll_hidden = collect_all("minecraft_launcher_lib")

analysis = Analysis(
    ["NullLauncher.py"],
    pathex=["."],
    binaries=mll_binaries,
    datas=mll_datas + [(str(ICON), "assets")],
    hiddenimports=mll_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="NullLauncher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON),
    version=str(VERSION_INFO),
)
