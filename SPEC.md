# maps3d_app SPEC (Bambu 3MF, zero manual)

## Goal
Windows app (PySide6) that takes a GPX track and produces a 120x120mm 3D terrain map with:
- terrain base (solid/watertight)
- track as red inlay (groove in base + separate inlay part)
- recessed frame
- optional AMS layers: water/green/detail (separate objects)

## Zero manual workflow
User only selects GPX (and output folder). Everything else is automatic:
1) Parse GPX lat/lon points
2) Compute bbox with margin
3) Download SRTM DEM for bbox (cached)
4) Generate model at exactly 120x120mm
5) Generate STL parts via Blender backend (preferred)
6) Pack parts into a single 3MF for Bambu Studio, with objects named:
   base, water, green, detail, track, frame
   and suggested RGBA colors:
   base=(120,80,50), water=(50,120,255), green=(40,180,80),
   detail=(230,220,200), track=(230,40,40), frame=(160,160,165)

## Constraints
- output must be watertight and in mm
- inlay clearance configurable, default 0.25mm
- groove depth default 1.6mm, width default 1.0mm
- frame recessed seat + finger notches supported
- Bambu Studio latest should import the 3MF as separate objects; colors are hints
