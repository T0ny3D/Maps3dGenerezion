from __future__ import annotations

import hashlib
import json
import os
import pkgutil
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import rasterio
from pyproj import Transformer

from .gpx_loader import load_gpx_points
from .pipeline import GenerateConfig, _compute_bbox, _model_horizontal_scale_mm_per_meter, estimate_relief_mm


def _resolve_blender_script_path() -> Path:
    base_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))
    candidates = [
        base_dir / "maps3d_app" / "engine" / "blender_script.py",
        base_dir / "engine" / "blender_script.py",
        Path(__file__).resolve().parent.parent / "engine" / "blender_script.py",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    embedded_script = pkgutil.get_data("maps3d_app.engine", "blender_script.py")
    if embedded_script:
        temp_script = Path(tempfile.gettempdir()) / "maps3d_app_blender_script.py"
        temp_script.write_bytes(embedded_script)
        return temp_script

    searched = "\n".join(f"- {p}" for p in candidates)
    raise FileNotFoundError(f"Script Blender non trovato. Percorsi controllati:\n{searched}")


def _inspect_blender_script(script_path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {"path": str(script_path), "exists": script_path.exists()}
    if not script_path.exists():
        return info

    raw = script_path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    info.update(
        {
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "has_track_debug_tag": "[maps3d][track_inlay]" in text,
            "has_curve_to_mesh_helper": "def _curve_to_mesh" in text,
            "has_resample_cap": "max_points" in text and "resample src_points" in text,
        }
    )
    return info

def _tail_text(text: str | None, lines: int = 40) -> str:
    if not text:
        return "<vuoto>"
    rows = text.rstrip().splitlines()
    return "\n".join(rows[-lines:])


def _append_run_log(log_path: Path, content: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", errors="replace") as fh:
        fh.write(content)
        if not content.endswith("\n"):
            fh.write("\n")


def _autodetect_blender_exe() -> str | None:
    if os.name == "nt":
        roots = [Path("C:/Program Files/Blender Foundation"), Path("C:/Program Files (x86)/Blender Foundation")]
        candidates: list[Path] = []
        for root in roots:
            if root.exists():
                candidates.extend(root.glob("Blender*/blender.exe"))
        if candidates:
            candidates.sort(reverse=True)
            return str(candidates[0])
    return shutil.which("blender")


def _compute_dem_metrics(
    gpx_path: str | Path,
    dem_path: str | Path,
    params: GenerateConfig,
) -> tuple[
    np.ndarray,
    np.ndarray,
    float,
    float,
    float,
    float,
    float,
    tuple[float, float, float, float],
    tuple[int, int],
]:
    points_lonlat = load_gpx_points(gpx_path)
    with rasterio.open(dem_path) as ds:
        if ds.crs is None:
            raise ValueError("Il DEM non ha CRS definito.")
        to_dem = Transformer.from_crs("EPSG:4326", ds.crs, always_xy=True)
        x_dem, y_dem = to_dem.transform(points_lonlat[:, 0], points_lonlat[:, 1])
        points_dem = np.column_stack((x_dem, y_dem))

        minx, miny, maxx, maxy = _compute_bbox(points_dem, params.bbox_margin_ratio)
        window = (
            rasterio.windows.from_bounds(minx, miny, maxx, maxy, transform=ds.transform)
            .round_offsets()
            .round_lengths()
        )

        data = ds.read(1, window=window, masked=True)
        if data.size == 0:
            raise ValueError("Ritaglio DEM vuoto: controlla GPX e DEM.")

        # ✅ fix: converti a float PRIMA di filled(np.nan)
        dem = np.asarray(data.astype(np.float64).filled(np.nan), dtype=np.float64)

        valid_mask = ~np.asarray(data.mask) if np.ma.isMaskedArray(data) else np.isfinite(dem)
        valid_mask &= np.isfinite(dem)
        if not np.any(valid_mask):
            raise ValueError("Ritaglio DEM privo di valori validi.")

        z_min_src = float(np.min(dem[valid_mask]))
        z_max_src = float(np.max(dem[valid_mask]))
        dem_filled = np.where(valid_mask, dem, z_min_src)
        z_range_src = max(z_max_src - z_min_src, 0.0)
        normalized01 = (
            np.zeros_like(dem_filled, dtype=np.float64)
            if z_range_src <= 1e-12
            else np.clip((dem_filled - z_min_src) / z_range_src, 0.0, 1.0)
        )

        rows, cols = dem_filled.shape
        win_t = ds.window_transform(window)
        x_coords = win_t.c + (np.arange(cols) + 0.5) * win_t.a
        y_coords = win_t.f + (np.arange(rows) + 0.5) * win_t.e
        x_min, x_max = float(np.min(x_coords)), float(np.max(x_coords))
        y_min, y_max = float(np.min(y_coords)), float(np.max(y_coords))
        dx = max(abs(x_max - x_min), 1e-6)
        dy = max(abs(y_max - y_min), 1e-6)

        if y_coords[0] > y_coords[-1]:
            normalized01 = np.flipud(normalized01)

        track_x_mm = (points_dem[:, 0] - min(x_min, x_max)) / dx * params.model_width_mm
        track_y_mm = (points_dem[:, 1] - min(y_min, y_max)) / dy * params.model_height_mm
        track_xy_mm = np.column_stack((track_x_mm, track_y_mm))

        horiz_scale_mm_per_meter = _model_horizontal_scale_mm_per_meter(
            ds, window, params.model_width_mm, params.model_height_mm
        )
        z_range_mm = (z_max_src - z_min_src) * horiz_scale_mm_per_meter

    return (
        normalized01,
        track_xy_mm,
        0.0,
        z_range_mm,
        z_range_mm,
        dx,
        dy,
        (x_min, y_min, x_max, y_max),
        (1 if x_max >= x_min else -1, 1 if y_max >= y_min else -1),
    )


def _fetch_osm_layers(points_lonlat: np.ndarray, model_w: float, model_h: float) -> dict[str, list[list[list[float]]]]:
    min_lon, min_lat = np.min(points_lonlat[:, 0]), np.min(points_lonlat[:, 1])
    max_lon, max_lat = np.min(points_lonlat[:, 0]), np.max(points_lonlat[:, 1])  # safe
    max_lon = float(np.max(points_lonlat[:, 0]))
    max_lat = float(np.max(points_lonlat[:, 1]))

    pad_lon = (max_lon - min_lon) * 0.12 + 1e-4
    pad_lat = (max_lat - min_lat) * 0.12 + 1e-4
    s, w, n, e = min_lat - pad_lat, min_lon - pad_lon, max_lat + pad_lat, max_lon + pad_lon

    q = f"""
[out:json][timeout:25];
(
  way["natural"="water"]({s},{w},{n},{e});
  way["waterway"]({s},{w},{n},{e});
  way["landuse"~"forest|meadow|grass"]({s},{w},{n},{e});
  way["leisure"~"park|garden"]({s},{w},{n},{e});
  way["highway"~"motorway|trunk|primary"]({s},{w},{n},{e});
);
out geom;
"""
    url = "https://overpass-api.de/api/interpreter?" + urlencode({"data": q})
    layers = {"water": [], "green": [], "detail": []}
    try:
        payload = json.loads(urlopen(url, timeout=30).read().decode("utf-8"))
    except Exception:
        return layers

    dx = max(e - w, 1e-9)
    dy = max(n - s, 1e-9)

    for el in payload.get("elements", []):
        geom = el.get("geometry", [])
        if len(geom) < 2:
            continue
        line = [[(p["lon"] - w) / dx * model_w, (p["lat"] - s) / dy * model_h] for p in geom]
        tags = el.get("tags", {})
        if tags.get("natural") == "water" or "waterway" in tags:
            layers["water"].append(line)
        elif tags.get("landuse") in {"forest", "meadow", "grass"} or tags.get("leisure") in {"park", "garden"}:
            layers["green"].append(line)
        elif tags.get("highway") in {"motorway", "trunk", "primary"}:
            layers["detail"].append(line)
    return layers


def _prepare_job_assets(
    gpx_path: str | Path,
    dem_path: str | Path,
    out_stl_path: str | Path,
    params: GenerateConfig,
) -> tuple[Path, Path]:
    normalized01, track_xy_mm, z_min_mm, z_max_mm, z_range_mm, _, _, _, _ = _compute_dem_metrics(
        gpx_path, dem_path, params
    )
    points_lonlat = load_gpx_points(gpx_path)
    osm_layers = _fetch_osm_layers(points_lonlat, params.model_width_mm, params.model_height_mm)

    job_dir = Path(tempfile.mkdtemp(prefix="maps3d_job_"))
    heightmap_path = job_dir / "heightmap.tif"
    job_json_path = job_dir / "job.json"

    base_out = Path(out_stl_path)
    suffix = "test_" if params.test_mode else ""
    out_map_stl_path = str(base_out.with_name(f"{base_out.stem}_{suffix}map.stl").resolve())
    out_frame_stl_path = str(base_out.with_name(f"{base_out.stem}_{suffix}frame.stl").resolve())
    out_base_stl_path = str(base_out.with_name(f"{base_out.stem}_{suffix}base_brown.stl").resolve())
    out_water_stl_path = str(base_out.with_name(f"{base_out.stem}_{suffix}water.stl").resolve())
    out_green_stl_path = str(base_out.with_name(f"{base_out.stem}_{suffix}green.stl").resolve())
    out_detail_stl_path = str(base_out.with_name(f"{base_out.stem}_{suffix}detail.stl").resolve())
    out_track_inlay_stl_path = str(base_out.with_name(f"{base_out.stem}_{suffix}track_inlay_red.stl").resolve())

    heightmap_u16 = np.round(normalized01 * 65535.0).astype(np.uint16)
    with rasterio.open(
        heightmap_path,
        "w",
        driver="GTiff",
        height=heightmap_u16.shape[0],
        width=heightmap_u16.shape[1],
        count=1,
        dtype="uint16",
    ) as out_ds:
        out_ds.write(heightmap_u16, 1)

    job: dict[str, Any] = {
        "size_mm_x": params.model_width_mm,
        "size_mm_y": params.model_height_mm,
        "base_mm": params.base_thickness_mm,
        "z_scale": float(params.vertical_scale),
        "z_min_mm": float(z_min_mm),
        "z_max_mm": float(z_max_mm * params.vertical_scale),
        "z_range_mm": float(z_range_mm),
        "track_height_mm": params.track_height_mm,
        "track_width_mm": 1.2,
        "grid_res": int(params.grid_res),
        "track_points_mm": track_xy_mm.tolist(),
        "heightmap_path": str(heightmap_path),
        "out_stl_path": str(Path(out_stl_path).resolve()),
        "out_map_stl_path": out_map_stl_path,
        "out_frame_stl_path": out_frame_stl_path,
        "out_base_stl_path": out_base_stl_path,
        "out_water_stl_path": out_water_stl_path,
        "out_green_stl_path": out_green_stl_path,
        "out_detail_stl_path": out_detail_stl_path,
        "out_track_inlay_stl_path": out_track_inlay_stl_path,
        "separate_frame": bool(params.separate_frame),
        "frame_wall_mm": float(params.frame_wall_mm),
        "frame_height_mm": float(params.frame_height_mm),
        "lip_depth_mm": float(params.lip_depth_mm),
        "clearance_mm": float(params.clearance_mm),
        "frame_text_enabled": bool(params.frame_text_enabled),
        "title_text": params.title_text,
        "subtitle_text": params.subtitle_text,
        "label_n": params.label_n,
        "label_s": params.label_s,
        "label_e": params.label_e,
        "label_w": params.label_w,
        "text_mode": params.text_mode,
        "text_depth_mm": float(params.text_depth_mm),
        "flush_mode": "recessed",
        "recess_mm": float(params.recess_mm),
        "lead_in_mm": float(params.lead_in_mm),
        "finger_notch_radius_mm": float(params.finger_notch_radius_mm),
        "rim_mm": float(params.rim_mm),
        "printer_profile": str(params.printer_profile),
        "test_mode": bool(params.test_mode),
        "test_size_mm": float(params.test_size_mm),
        "ams_enabled": bool(params.ams_enabled),
        "track_inlay_enabled": bool(params.track_inlay_enabled),
        "groove_width_mm": float(params.groove_width_mm),
        "groove_depth_mm": float(params.groove_depth_mm),
        "groove_chamfer_mm": float(params.groove_chamfer_mm),
        "track_clearance_mm": float(params.track_clearance_mm),
        "track_relief_mm": float(params.track_relief_mm),
        "track_top_radius_mm": float(params.track_top_radius_mm),
        "osm_water_lines_mm": osm_layers.get("water", []),
        "osm_green_lines_mm": osm_layers.get("green", []),
        "osm_detail_lines_mm": osm_layers.get("detail", []),
    }

    job_json_path.write_text(json.dumps(job, indent=2), encoding="utf-8")
    return job_dir, job_json_path


def run_blender_pipeline(
    gpx_path: str | Path,
    dem_path: str | Path,
    out_stl_path: str | Path,
    params: GenerateConfig,
    blender_exe_path: str | None = None,
) -> None:
    blender_exe = blender_exe_path or _autodetect_blender_exe()
    if not blender_exe:
        raise ValueError("Blender non trovato. Specifica il percorso di blender.exe nella UI.")

    blender_script = _resolve_blender_script_path()
    blender_script_info = _inspect_blender_script(blender_script)

    job_dir, job_json = _prepare_job_assets(gpx_path, dem_path, out_stl_path, params)

    model_relief_mm = estimate_relief_mm(gpx_path, dem_path, params)
    max_model_span_mm = max(float(params.model_width_mm), float(params.model_height_mm), 1.0)
    if model_relief_mm > max_model_span_mm * 5.0:
        raise ValueError(
            "Rilievo DEM fuori scala per il modello: "
            f"relief={model_relief_mm:.2f} mm, "
            f"size={params.model_width_mm:.2f}x{params.model_height_mm:.2f} mm. "
            "Controlla CRS/unità del DEM o riduci la scala verticale."
        )

    job_log_file = Path(job_dir) / "blender_run.log"
    output_log_file = Path(out_stl_path).resolve().parent / "blender_run.log"

    # DEBUG: così lo vedi nel log della UI
    print(f"[blender] Job dir: {job_dir}")
    print(f"[blender] Job json: {job_json}")
    print(f"[blender] Blender script: {blender_script}")
    print(f"[blender] Blender script info: {blender_script_info}")
    print(f"[blender] Blender log (job): {job_log_file}")
    print(f"[blender] Blender log (output): {output_log_file}")

    if not job_json.exists():
        raise RuntimeError(f"Job JSON non creato: {job_json}")

    cmd = [
        str(blender_exe),
        "--background",
        "--factory-startup",
        "--python-exit-code", "1",
        "--log-file", str(job_log_file),
        "--log-level", "2",
        "--python", str(blender_script),
        "--", str(job_json),
    ]

    command_str = " ".join(cmd)
    _append_run_log(
        output_log_file,
        "\n".join([
            "=== Blender run ===",
            f"command: {command_str}",
            f"blender_script: {blender_script}",
            f"blender_script_info: {blender_script_info}",
            f"job_json: {job_json}",
            f"job_log: {job_log_file}",
        ]),
    )

    result = subprocess.run(cmd, capture_output=True, text=True)

    stdout_tail = _tail_text(result.stdout)
    stderr_tail = _tail_text(result.stderr)
    _append_run_log(
        output_log_file,
        "\n".join([
            f"returncode: {result.returncode}",
            "--- STDOUT (tail) ---",
            stdout_tail,
            "--- STDERR (tail) ---",
            stderr_tail,
            f"job_log_file: {job_log_file}",
            "=== End Blender run ===",
        ]),
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Blender pipeline fallita.\n"
            f"Comando: {command_str}\n"
            f"Script Blender: {blender_script}\n"
            f"Job JSON: {job_json}\n"
            f"Return code: {result.returncode}\n"
            f"STDOUT (ultime righe):\n{stdout_tail}\n"
            f"STDERR (ultime righe):\n{stderr_tail}\n"
            f"Log salvato: {output_log_file}\n"
            f"Log Blender (--log-file): {job_log_file}"
        )

    # verifica output STL
    base_out = Path(out_stl_path).resolve()
    suffix = "test_" if params.test_mode else ""
    expected = [
        base_out.with_name(f"{base_out.stem}_{suffix}base_brown.stl"),
        base_out.with_name(f"{base_out.stem}_{suffix}water.stl"),
        base_out.with_name(f"{base_out.stem}_{suffix}green.stl"),
        base_out.with_name(f"{base_out.stem}_{suffix}detail.stl"),
        base_out.with_name(f"{base_out.stem}_{suffix}track_inlay_red.stl"),
    ]
    if params.separate_frame:
        expected.append(base_out.with_name(f"{base_out.stem}_{suffix}frame.stl"))

    missing = [p for p in expected if (not p.exists()) or p.stat().st_size == 0]
    if missing:
        raise RuntimeError(
            "Blender ha terminato senza errori ma NON ha generato gli STL attesi.\n"
            "File mancanti/vuoti:\n"
            + "\n".join(f"- {p}" for p in missing)
            + "\n\nComando: " + command_str
            + "\nScript Blender: " + str(blender_script)
            + "\nJob JSON: " + str(job_json)
            + "\nReturn code: " + str(result.returncode)
            + "\n\nSTDOUT (ultime righe):\n"
            + stdout_tail
            + "\n\nSTDERR (ultime righe):\n"
            + stderr_tail
            + f"\n\nJob dir: {job_dir}"
            + f"\nLog salvato: {output_log_file}"
            + f"\nLog Blender (--log-file): {job_log_file}"
        )
