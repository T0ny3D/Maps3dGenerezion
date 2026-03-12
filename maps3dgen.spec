# -*- mode: python ; coding: utf-8 -*-
# sanitized for merge artifacts

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

hiddenimports: list[str] = []
hiddenimports += collect_submodules("maps3d_app")
hiddenimports += collect_submodules("maps3d_app.core")
hiddenimports += [
    "maps3d_app.core.blender_backend",
    "maps3d_app.core.mesh_builder",
    "maps3d_app.core.pipeline",
    "maps3d_app.ui.main_window",
]


hiddenimports = collect_submodules("maps3d_app")
hiddenimports.append("maps3d_app.core.blender_backend")
hiddenimports.append("maps3d_app.core.pipeline")
 main
 main

# Keep deterministic order and remove duplicates.
hiddenimports = list(dict.fromkeys(hiddenimports))

datas = [
    ("maps3d_app/engine/blender_script.py", "maps3d_app/engine"),
]
binaries = []

for pkg in ("rasterio", "pyproj", "shapely"):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

hiddenimports = list(dict.fromkeys(hiddenimports))
datas = list(dict.fromkeys(datas))
binaries = list(dict.fromkeys(binaries))

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Maps3DGen_NEW",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
