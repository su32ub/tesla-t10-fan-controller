import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

SPEC_DIR = Path(SPECPATH)
ROOT = SPEC_DIR.parent

hidden = []
try:
    hidden = collect_submodules("pyghmi")
except Exception:
    hidden = []

a = Analysis(
    [str(SPEC_DIR / "entry_desktop.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "gpu_fan_controller" / "assets"), "gpu_fan_controller/assets"),
        (str(ROOT / "gpu_fan_controller" / "static"), "gpu_fan_controller/static"),
        (str(ROOT / "config.example.json"), "."),
    ],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="gpu-fan-controller-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(SPEC_DIR / "icon.ico") if sys.platform == "win32" else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="gpu-fan-controller-gui",
)
