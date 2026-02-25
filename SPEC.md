# maps3d_app SPEC (Bambu 3MF, zero manual)

## Goal
Windows app (PySide6) that takes a GPX track and produces a **120x120mm** 3D terrain map with:
- terrain base (solid/watertight)
- track as **red inlay** (groove in base + separate inlay part)
- **recessed frame** (incasso / seat)
- optional AMS layers: water/green/detail (separate objects)

## Zero manual workflow
User only selects:
- a GPX file
- an output folder (optional; if missing, use GPX folder)

Everything else is automatic:
1. Parse GPX lat/lon points
2. Compute bbox with margin
3. Download SRTM DEM for bbox (cached)
4. Generate model at exactly **120x120mm**
5. Generate STL parts via **Blender backend** (preferred)
6. Pack parts into a single **Bambu Studio 3MF** with objects named:
   - base, water, green, detail, track, frame
   and suggested RGBA colors:
   - base=(120,80,50)
   - water=(50,120,255)
   - green=(40,180,80)
   - detail=(230,220,200)
   - track=(230,40,40)
   - frame=(160,160,165)

## Output naming (STL parts)
Blender backend must produce these files (some may be missing if disabled):
- `{output_base}_base_brown.stl`
- `{output_base}_water.stl`
- `{output_base}_green.stl`
- `{output_base}_detail.stl`
- `{output_base}_track_inlay_red.stl`
- `{output_base}_frame.stl`

Test mode adds suffix `_test` before extension.

## Constraints
- output must be watertight and in **mm**
- inlay clearance configurable, default **0.25mm**
- groove depth default **1.6mm**, groove width default **1.0mm**
- frame recessed seat + finger notches supported
- Bambu Studio latest should import the 3MF as separate objects; embedded colors are **hints**

## Blender requirement
Blender is the preferred geometry backend (boolean groove + inlay + frame + optional OSM layers).
The app should:
- auto-detect Blender if possible (common install paths),
- otherwise allow selecting `blender.exe` once and remember it.
If Blender is not available, fall back to the pure Python pipeline (terrain + simple raised track).
