from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
config = (ROOT / "null_launcher" / "config.py").read_text(encoding="utf-8")
match = re.search(r'^APP_VERSION\s*=\s*"([0-9]+(?:\.[0-9]+){1,3})"', config, re.MULTILINE)
if not match:
    raise SystemExit("APP_VERSION not found")
version = match.group(1)
parts = [int(x) for x in version.split(".")]
while len(parts) < 4:
    parts.append(0)
parts = parts[:4]
file_version = ".".join(str(x) for x in parts)
tuple_text = ", ".join(str(x) for x in parts)
text = f'''VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({tuple_text}),
    prodvers=({tuple_text}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'BrawliPup12'),
          StringStruct('FileDescription', 'Terminal Minecraft Launcher'),
          StringStruct('FileVersion', '{file_version}'),
          StringStruct('InternalName', 'NullLauncher'),
          StringStruct('OriginalFilename', 'NullLauncher.exe'),
          StringStruct('ProductName', 'NullLauncher'),
          StringStruct('ProductVersion', '{file_version}'),
          StringStruct('LegalCopyright', 'Copyright (C) 2026 BrawliPup12')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
'''
out = ROOT / "assets" / "version_info.txt"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(text, encoding="utf-8")
print(f"Synced Windows version metadata: {file_version}")
