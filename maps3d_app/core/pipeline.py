from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
import trimesh

from .gpx_loader import load_gpx_points
from .mesh_builder import build_terrain_mesh, build_track_mesh


@dataclass
class GenerateConfig:
    model_width_mm: float = 150.0
    model_height_mm: float = 150.0
    base_thickness_mm: float = 5.0
    vertical_scale: float = 1.0
    track_height_mm: float = 2.0
    bbox_margin_ratio: float = 0.10
    grid_res: int = 400

    separate_frame: bool = True
    frame_text_enabled: bool = True
    frame_wall_mm: float = 10.0
    frame_height_mm: float = 8.0
    lip_depth_mm: float = 3.0
    clearance_mm: float = 0.3
    text_mode: str = "inciso"
    text_depth_mm: float = 1.2
    title_text: str = ""
    subtitle_text: str = ""
    label_n: str = "N"
    label_s: str = "S"
    label_e: str = "E"
    label_w: str = "O"

    flush_mode: str = "recessed"
    recess_mm: float = 1.5
    lead_in_mm: float = 1.0
    finger_notch_radius_mm: float = 7.0
    rim_mm: float = 3.0
    printer_profile: str = "custom"

    test_mode: bool = False
    test_size_mm: float = 40.0

    ams_enabled: bool = True
    track_inlay_enabled: bool = True
    groove_width_mm: float = 2.6
    groove_depth_mm: float = 1.6
    groove_chamfer_mm: float = 0.4
    track_clearance_mm: float = 0.20
    track_relief_mm: float = 0.6
    track_top_radius_mm: float = 0.8


def _compute_bbox(points: np.ndarray, margin_ratio: float) -> tuple[float, float, float, float]:
    minx, miny = points.min(axis=0)
    maxx, maxy = points.max(axis=0)
    dx = max(maxx - minx, 1e-6)
    dy = max(maxy - miny, 1e-6)
    mx = dx * margin_ratio
    my = dy * margin_ratio
    return minx - mx, miny - my, maxx + mx, maxy + my


def run_python_pipeline(
    gpx_path: str | Path,
    dem_path: str | Path,
    stl_output_path: str | Path,
    config: GenerateConfig,
) -> None:
    points_lonlat = load_gpx_points(gpx_path)

    with rasterio.open(dem_path) as ds:
        if ds.crs is None:
            raise ValueError("Il DEM non ha CRS definito.")

        to_dem = Transformer.from_crs("EPSG:4326", ds.crs, always_xy=True)
        x_dem, y_dem = to_dem.transform(points_lonlat[:, 0], points_lonlat[:, 1])
        points_dem = np.column_stack((x_dem, y_dem))

        minx, miny, maxx, maxy = _compute_bbox(points_dem, config.bbox_margin_ratio)

        window = rasterio.windows.from_bounds(minx, miny, maxx, maxy, transform=ds.transform)
        window = window.round_offsets().round_lengths()

        data = ds.read(1, window=window, masked=True)
        if data.size == 0:
            raise ValueError("Ritaglio DEM vuoto: controlla GPX e DEM.")

        dem = data.filled(np.nan).astype(np.float64)
        finite = np.isfinite(dem)
        if not np.any(finite):
            raise ValueError("Ritaglio DEM privo di valori validi.")

        min_elev = float(np.nanmin(dem))
        dem = np.where(np.isfinite(dem), dem, min_elev)

        win_t = ds.window_transform(window)
        rows, cols = dem.shape
        cols_idx = np.arange(cols)
        rows_idx = np.arange(rows)

        x_coords = win_t.c + (cols_idx + 0.5) * win_t.a
        y_coords = win_t.f + (rows_idx + 0.5) * win_t.e

        x_min, x_max = float(np.min(x_coords)), float(np.max(x_coords))
        y_min, y_max = float(np.min(y_coords)), float(np.max(y_coords))
        dx = max(abs(x_max - x_min), 1e-6)
        dy = max(abs(y_max - y_min), 1e-6)

        x_mm = (x_coords - min(x_min, x_max)) / dx * config.model_width_mm
        y_mm = (y_coords - min(y_min, y_max)) / dy * config.model_height_mm
        y_mm = np.sort(y_mm)
        if y_coords[0] > y_coords[-1]:
            dem = np.flipud(dem)

        horiz_scale_mm_per_unit = min(config.model_width_mm / dx, config.model_height_mm / dy)
        z_mm = (dem - min_elev) * horiz_scale_mm_per_unit * config.vertical_scale

        track_x_mm = (points_dem[:, 0] - min(x_min, x_max)) / dx * config.model_width_mm
        track_y_mm = (points_dem[:, 1] - min(y_min, y_max)) / dy * config.model_height_mm
        track_xy_mm = np.column_stack((track_x_mm, track_y_mm))

    terrain_mesh = build_terrain_mesh(x_mm=x_mm, y_mm=y_mm, z_mm=z_mm, base_thickness_mm=config.base_thickness_mm)
    track_mesh = build_track_mesh(
        track_xy_mm=track_xy_mm,
        x_mm=x_mm,
        y_mm=y_mm,
        z_mm=z_mm,
        track_height_mm=config.track_height_mm,
    )

    final_mesh = trimesh.util.concatenate([terrain_mesh, track_mesh])
    if final_mesh.faces.shape[0] == 0:
        raise ValueError("Mesh vuota, impossibile esportare STL.")

    Path(stl_output_path).parent.mkdir(parents=True, exist_ok=True)
    final_mesh.export(stl_output_path)


def run_pipeline(
    gpx_path: str | Path,
    dem_path: str | Path,
    stl_output_path: str | Path,
    config: GenerateConfig,
    backend: str = "blender",
    blender_exe_path: str | None = None,
) -> None:
    backend_norm = backend.strip().lower()
    if backend_norm == "python":
        run_python_pipeline(gpx_path, dem_path, stl_output_path, config)
        return

    if backend_norm == "blender":
        from .blender_backend import run_blender_pipeline

        run_blender_pipeline(
            gpx_path=gpx_path,
            dem_path=dem_path,
            out_stl_path=stl_output_path,
            params=config,
            blender_exe_path=blender_exe_path,
        )
        return

    raise ValueError(f"Backend non supportato: {backend}. Usa 'python' o 'blender'.")


# Backward compatibility

generate_stl_from_gpx_dem = run_python_pipeline


def compute_gpx_bbox_lonlat(gpx_path: str | Path, margin_ratio: float = 0.20) -> tuple[float, float, float, float]:
    points_lonlat = load_gpx_points(gpx_path)
    return _compute_bbox(points_lonlat, margin_ratio)


def default_dem_output_path_for_gpx(gpx_path: str | Path) -> Path:
    gpx = Path(gpx_path)
    return gpx.parent / "output" / "dem_srtm.tif"
