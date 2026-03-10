# Python-first STL Pipeline Plan

## Objectives

Move from a Blender-centric geometry workflow to a deterministic Python mesh pipeline that:

1. Uses **one canonical XY model coordinate system** from ingest to STL export.
2. Generates **printable meshes directly in Python** for:
   - base
   - water
   - green
   - detail
   - track
   - frame
3. Keeps Blender out of the critical path (optional legacy fallback only during migration).
4. Clips every layer to the model footprint before export.
5. Exports separate STL parts in a stable, reproducible order.
6. Preserves modularity for future 3MF assembly.

---

## Proposed architecture

## 1) Canonical model-space contract

Create a strict contract shared by all geometry stages:

- XY units: **millimeters**, origin at model lower-left `(0, 0)`.
- Bounding box: `[0, model_width_mm] x [0, model_height_mm]`.
- Z units: **millimeters** from base datum.
- Terrain sampling: regular DEM grid resampled once into model-space arrays.

Core dataclasses:

- `ModelFrame`:
  - model dimensions
  - source CRS and transform helpers
  - footprint polygon in model XY
- `TerrainField`:
  - `x_mm`, `y_mm`, `z_mm`
  - interpolation helpers (`sample_z`)
- `LayerGeometry`:
  - input vectors/polygons in model XY for each thematic layer

## 2) Pipeline stages (Python-only)

1. **Ingest**
   - GPX + DEM + OSM vectors.
2. **Normalize / transform**
   - Reproject all source data to DEM CRS.
   - Convert to canonical model XY once.
3. **Build terrain/base**
   - Generate watertight terrain solid directly from DEM field.
4. **Build thematic solids**
   - `water`, `green`, `detail`: polygon extrusion/surface offsets in Python.
   - `track`: buffered path sweep/inlay mesh in Python.
   - `frame`: parametric frame mesh built from footprint.
5. **Clip**
   - Clip vectors and final meshes to footprint before writing.
6. **Export**
   - Deterministic per-part STL files + metadata manifest.

## 3) Module layout (target)

- `maps3d_app/core/model_space.py`
  - canonical frame creation and transforms
- `maps3d_app/core/terrain_field.py`
  - DEM crop/resample, elevation normalization, sampling
- `maps3d_app/core/layer_sources.py`
  - GPX/OSM ingestion and semantic layer extraction
- `maps3d_app/core/layer_meshing.py`
  - per-layer mesh generation utilities
- `maps3d_app/core/clipper.py`
  - footprint clipping helpers (2D + optional mesh clipping helpers)
- `maps3d_app/core/export_stl.py`
  - deterministic naming/order + manifest writing
- `maps3d_app/core/pipeline.py`
  - orchestration only

Keep `blender_backend.py` as temporary compatibility adapter, not default path.

---

## File/module changes introduced in this patch

1. **Default backend switched to Python** in pipeline runtime entrypoint.
2. **UI default backend switched to Python-first** while retaining Blender as optional legacy backend.
3. **README updated** to describe Python as primary path and Blender as compatibility fallback.
4. **This architecture/migration plan document added** for implementation sequencing.

---

## Exact patch plan (implementation roadmap)

## Phase 0 — Safety rails (small, immediate)

- [ ] Make Python backend the default across UI + API.
- [ ] Keep Blender backend callable only by explicit opt-in.
- [ ] Add a per-run manifest (`*.json`) with part filenames + hashes.

## Phase 1 — Canonical frame extraction

- [ ] Introduce `ModelFrame` from GPX/DEM crop bounds.
- [ ] Replace ad-hoc XY scaling logic with shared transforms used by all layers.
- [ ] Add unit tests for transform round-trip and bounds mapping.

## Phase 2 — Terrain/base solid in Python

- [ ] Move DEM processing into `TerrainField` utilities.
- [ ] Generate watertight base/terrain mesh with deterministic triangulation.
- [ ] Add mesh validity checks (`is_watertight`, no NaN/inf, non-empty).

## Phase 3 — Layer generation in Python

- [ ] OSM/GPX vectors normalized to model XY.
- [ ] Water/green/detail built from robust polygon buffering/extrusion.
- [ ] Track built as explicit inlay mesh from buffered centerline.
- [ ] Clip all layer geometries to footprint before triangulation.

## Phase 4 — Frame generation in Python

- [ ] Create parametric frame generator from footprint and existing frame config.
- [ ] Export frame as independent STL with deterministic orientation.

## Phase 5 — Deterministic export + regression checks

- [ ] Stable file naming:
  - `*_base.stl`
  - `*_water.stl`
  - `*_green.stl`
  - `*_detail.stl`
  - `*_track.stl`
  - `*_frame.stl`
- [ ] Deterministic mesh write ordering and optional SHA256 manifest.
- [ ] Golden-run regression test on a fixed GPX+DEM fixture.

## Phase 6 — Optional future 3MF assembly

- [ ] Keep 3MF builder separate, consuming STL manifest only.
- [ ] No geometry generation inside 3MF stage.

---

## Minimal migration strategy from current Blender pipeline

1. **Default switch now**: Python backend selected by default.
2. **Parallel run period**: keep Blender backend for comparison only.
3. **Feature parity checkpoints**:
   - terrain/base parity
   - track parity
   - water/green/detail parity
   - frame parity
4. **Verification gates** (per checkpoint):
   - mesh validity
   - part count and naming
   - deterministic hash stability on repeated run
5. **Blender deprecation**:
   - once parity + stability pass, hide Blender option behind advanced flag or remove from UI.

---

## Notes on robustness

- Avoid boolean-heavy mesh ops where possible.
- Prefer 2D clipping + clean extrusion to reduce non-manifold failures.
- Keep numerical tolerances centralized (`epsilon_mm`, clip tolerances).
- Enforce deterministic sorting of input geometries prior to triangulation/export.
