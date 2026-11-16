# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent
datas = [
    (str(root / "UI" / "static"), "UI/static"),
    (str(root / "pictures"), "pictures"),
    (str(root / "build" / "windows-models"), "models"),
]
a = Analysis(
    [str(root / "windows" / "launcher.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("bots") + [
        "uvicorn.loops.asyncio", "uvicorn.protocols.http.h11_impl", "uvicorn.lifespan.on",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["torch", "training", "pytest", "IPython", "matplotlib", "scipy", "pandas", "numpy.f2py", "tkinter", "PyQt6", "PyQt5", "PySide2"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="算术麻将",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(root / "pictures" / "app_icon.ico"),
)
