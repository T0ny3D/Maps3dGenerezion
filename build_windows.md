# Building Windows Executable with PyInstaller

This guide explains how to build and validate the Windows `.exe` for **Maps3DGen**.

## Prerequisites
- Windows 10/11
- Python 3.10+ in PATH
- PowerShell
- Blender installed (required for Blender backend smoke test)
- Bambu Studio installed (recommended for 3MF validation)

## 1) Build
```powershell
git clone https://github.com/T0ny3D/Maps3dGenerezion.git
cd Maps3dGenerezion
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

pyinstaller --noconfirm --onefile --windowed --name Maps3DGen_NEW `
  --paths . `
  launcher.py `
  --collect-submodules maps3d_app `
  --hidden-import maps3d_app.core.blender_backend `
  --collect-all rasterio `
  --collect-all pyproj `
  --collect-all shapely `
  --add-data "maps3d_app/engine/blender_script.py;maps3d_app/engine"
```

Expected output:
- `dist\Maps3DGen_NEW.exe`

## 2) Quick payload verification (before runtime)
Use PyInstaller archive viewer to verify the EXE contains both Blender backend module and Blender script resource.

```powershell
$archive = pyi-archive_viewer -r dist\Maps3DGen_NEW.exe | Out-String
$archive | Out-File dist\pyi-archive.txt -Encoding utf8
$archive -match "maps3d_app.core.blender_backend"
$archive -match "blender_script.py"
```

Both checks must return `True`.

## 3) Manual end-to-end smoke test on Windows (Blender backend + 3MF)
1. Launch `dist\Maps3DGen_NEW.exe`.
2. Select a known-good GPX file.
3. Keep backend on **Blender (consigliato)**.
4. Set a valid `blender.exe` path (if autodetect fails).
5. Enable **Genera anche 3MF (Bambu)**.
6. Choose output folder and start generation.
7. Verify outputs exist and are non-empty:
   - `*_base_brown.stl`
   - `*_track_inlay_red.stl`
   - `*_frame.stl` (if separate frame enabled)
   - optional AMS layers (`*_water.stl`, `*_green.stl`, `*_detail.stl`)
   - `.3mf` output file
8. Open generated `.3mf` in Bambu Studio and confirm objects are separate/imported correctly.
9. Review UI log and generated `blender_run.log` (in job temp dir if reported by UI) for backend errors.

## 4) Release-readiness checklist
- [ ] EXE built from tagged commit
- [ ] payload checks for `blender_backend` and `blender_script.py` passed
- [ ] manual Blender-backend generation produced non-empty STL files
- [ ] 3MF opens in Bambu Studio with expected parts
- [ ] no `No module named 'maps3d_app.core.blender_backend'` at runtime
- [ ] no `Script Blender non trovato` at runtime
