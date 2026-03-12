from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import rasterio
from pyproj import Geod, Transformer
import trimesh
from shapely.geometry import GeometryCollection, LineString, MultiLineString, box

from .gpx_loader import load_gpx_points
from .mesh_builder import build_line_layer_mesh, build_rect_frame_mesh, build_terrain_mesh
from .model_space import ModelSpace

_WGS84_GEOD = Geod(ellps="WGS84")


def _model_horizontal_scale_mm_per_meter(ds: rasterio.io.DatasetReader, window: rasterio.windows.Window, model_width_mm: float, model_height_mm: float) -> float:
    win_t = ds.window_transform(window)
    rows = int(window.height)
    cols = int(window.width)

    x_coords = win_t.c + (np.arange(cols) + 0.5) * win_t.a
    y_coords = win_t.f + (np.arange(rows) + 0.5) * win_t.e

    dx_units = max(abs(float(np.max(x_coords)) - float(np.min(x_coords))), 1e-9)
    dy_units = max(abs(float(np.max(y_coords)) - float(np.min(y_coords))), 1e-9)

    if ds.crs is not None and ds.crs.is_projected:
        unit_factor = float(getattr(ds.crs, "linear_units_factor", 1.0) or 1.0)
        span_x_m = max(dx_units * unit_factor, 1e-6)
        span_y_m = max(dy_units * unit_factor, 1e-6)
    else:
        left, bottom, right, top = rasterio.windows.bounds(window, ds.transform)
        if ds.crs is not None and str(ds.crs).upper() != "EPSG:4326":
            to_lonlat = Transformer.from_crs(ds.crs, "EPSG:4326", always_xy=True)
            lons, lats = to_lonlat.transform([left, right, left, right], [bottom, bottom, top, top])
            left, right = float(min(lons)), float(max(lons))
            bottom, top = float(min(lats)), float(max(lats))

        mid_lat = (bottom + top) * 0.5
        _, _, span_x_m = _WGS84_GEOD.inv(left, mid_lat, right, mid_lat)
        _, _, span_y_m = _WGS84_GEOD.inv((left + right) * 0.5, bottom, (left + right) * 0.5, top)
        span_x_m = max(abs(float(span_x_m)), 1e-6)
        span_y_m = max(abs(float(span_y_m)), 1e-6)

    return min(model_width_mm / span_x_m, model_height_mm / span_y_m)


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


def _python_output_paths(stl_output_path: str | Path, test_mode: bool) -> dict[str, Path]:
    out = Path(stl_output_path)
    suffix = "_test" if test_mode else ""
    stem = out.stem
    parent = out.parent
    return {
        "base": parent / f"{stem}{suffix}_base_brown.stl",
        "water": parent / f"{stem}{suffix}_water.stl",
        "green": parent / f"{stem}{suffix}_green.stl",
        "detail": parent / f"{stem}{suffix}_detail.stl",
        "track": parent / f"{stem}{suffix}_track_inlay_red.stl",
        "frame": parent / f"{stem}{suffix}_frame.stl",
        "combined": out,
    }


def _clip_polyline_to_footprint(track_xy_mm: np.ndarray, model_width_mm: float, model_height_mm: float) -> list[np.ndarray]:
    if len(track_xy_mm) < 2:
        return []

    footprint = box(0.0, 0.0, model_width_mm, model_height_mm)
    clipped = LineString(track_xy_mm).intersection(footprint)

    def _extract_segments(geom: object) -> list[np.ndarray]:
        if isinstance(geom, LineString):
            coords = np.asarray(geom.coords, dtype=np.float64)
            return [coords] if len(coords) >= 2 else []
        if isinstance(geom, MultiLineString):
            segs: list[np.ndarray] = []
            for line in geom.geoms:
                coords = np.asarray(line.coords, dtype=np.float64)
                if len(coords) >= 2:
                    segs.append(coords)
            return segs
        if isinstance(geom, GeometryCollection):
            segs: list[np.ndarray] = []
            for child in geom.geoms:
                segs.extend(_extract_segments(child))
            return segs
        return []

    return _extract_segments(clipped)


def _fetch_osm_line_layers(points_lonlat: np.ndarray, to_dem: Transformer, model_space: ModelSpace) -> dict[str, list[np.ndarray]]:
    min_lon = float(np.min(points_lonlat[:, 0]))
    min_lat = float(np.min(points_lonlat[:, 1]))
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
  way["highway"~"motorway|trunk|primary|secondary"]({s},{w},{n},{e});
);
out geom;
"""
    layers = {"water": [], "green": [], "detail": []}

    url = "https://overpass-api.de/api/interpreter?" + urlencode({"data": q})
    try:
        payload = json.loads(urlopen(url, timeout=30).read().decode("utf-8"))
    except Exception:
        return layers

    for el in payload.get("elements", []):
        geom = el.get("geometry", [])
        if len(geom) < 2:
            continue

        lons = [float(p["lon"]) for p in geom]
        lats = [float(p["lat"]) for p in geom]
        xs_dem, ys_dem = to_dem.transform(lons, lats)
        src_xy = np.column_stack((np.asarray(xs_dem, dtype=np.float64), np.asarray(ys_dem, dtype=np.float64)))
        model_xy = model_space.to_model_xy(src_xy)

        tags = el.get("tags", {})
        if tags.get("natural") == "water" or "waterway" in tags:
            layers["water"].append(model_xy)
        elif tags.get("landuse") in {"forest", "meadow", "grass"} or tags.get("leisure") in {"park", "garden"}:
            layers["green"].append(model_xy)
        elif tags.get("highway") in {"motorway", "trunk", "primary", "secondary"}:
            layers["detail"].append(model_xy)

    return layers


def _export_mesh_or_remove(path: Path, mesh: trimesh.Trimesh) -> None:
    if mesh.faces.shape[0] > 0:
        mesh.export(path)
        return
    if path.exists():
        path.unlink()


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

        model_space = ModelSpace.from_source_bounds(
            src_min_x=x_min,
            src_max_x=x_max,
            src_min_y=y_min,
            src_max_y=y_max,
            model_width_mm=config.model_width_mm,
            model_height_mm=config.model_height_mm,
        )

        x_mm = model_space.to_model_x(x_coords)
        y_mm = model_space.to_model_y(y_coords)

        if x_mm[0] > x_mm[-1]:
            x_mm = x_mm[::-1]
            dem = np.fliplr(dem)
        if y_mm[0] > y_mm[-1]:
            y_mm = y_mm[::-1]
            dem = np.flipud(dem)

        horiz_scale_mm_per_meter = _model_horizontal_scale_mm_per_meter(ds, window, config.model_width_mm, config.model_height_mm)
        z_mm = (dem - min_elev) * horiz_scale_mm_per_meter * config.vertical_scale

        track_xy_mm = model_space.to_model_xy(points_dem)
        osm_layers = _fetch_osm_line_layers(points_lonlat, to_dem=to_dem, model_space=model_space)

    terrain_mesh = build_terrain_mesh(x_mm=x_mm, y_mm=y_mm, z_mm=z_mm, base_thickness_mm=config.base_thickness_mm)

    clipped_track_segments = _clip_polyline_to_footprint(track_xy_mm, config.model_width_mm, config.model_height_mm)
    track_mesh = build_line_layer_mesh(
        line_segments_xy_mm=clipped_track_segments,
        x_mm=x_mm,
        y_mm=y_mm,
        z_mm=z_mm,
        layer_height_mm=config.track_height_mm,
        layer_width_mm=1.2,
    )

    clipped_water_segments = [
        seg
        for src in osm_layers["water"]
        for seg in _clip_polyline_to_footprint(src, config.model_width_mm, config.model_height_mm)
    ]
    clipped_green_segments = [
        seg
        for src in osm_layers["green"]
        for seg in _clip_polyline_to_footprint(src, config.model_width_mm, config.model_height_mm)
    ]
    clipped_detail_segments = [
        seg
        for src in osm_layers["detail"]
        for seg in _clip_polyline_to_footprint(src, config.model_width_mm, config.model_height_mm)
    ]

    water_mesh = build_line_layer_mesh(
        line_segments_xy_mm=clipped_water_segments,
        x_mm=x_mm,
        y_mm=y_mm,
        z_mm=z_mm,
        layer_height_mm=0.7,
        layer_width_mm=1.8,
    )
    green_mesh = build_line_layer_mesh(
        line_segments_xy_mm=clipped_green_segments,
        x_mm=x_mm,
        y_mm=y_mm,
        z_mm=z_mm,
        layer_height_mm=0.5,
        layer_width_mm=1.4,
    )
    detail_mesh = build_line_layer_mesh(
        line_segments_xy_mm=clipped_detail_segments,
        x_mm=x_mm,
        y_mm=y_mm,
        z_mm=z_mm,
        layer_height_mm=0.4,
        layer_width_mm=0.9,
    )

    frame_mesh = (
        build_rect_frame_mesh(
            model_width_mm=config.model_width_mm,
            model_height_mm=config.model_height_mm,
            frame_wall_mm=config.frame_wall_mm,
            frame_height_mm=config.frame_height_mm,
            clearance_mm=config.clearance_mm,
            base_thickness_mm=config.base_thickness_mm,
        )
        if config.separate_frame
        else trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3), dtype=np.int64), process=False)
    )

    if terrain_mesh.faces.shape[0] == 0:
        raise ValueError("Mesh base vuota, impossibile esportare STL.")

    out_paths = _python_output_paths(stl_output_path, config.test_mode)
    out_paths["base"].parent.mkdir(parents=True, exist_ok=True)

    _export_mesh_or_remove(out_paths["base"], terrain_mesh)
    _export_mesh_or_remove(out_paths["track"], track_mesh)
    _export_mesh_or_remove(out_paths["water"], water_mesh)
    _export_mesh_or_remove(out_paths["green"], green_mesh)
    _export_mesh_or_remove(out_paths["detail"], detail_mesh)
    _export_mesh_or_remove(out_paths["frame"], frame_mesh)

    combined_meshes = [terrain_mesh]
    for mesh in (track_mesh, water_mesh, green_mesh, detail_mesh):
        if mesh.faces.shape[0] > 0:
            combined_meshes.append(mesh)
    final_mesh = trimesh.util.concatenate(combined_meshes)
    final_mesh.export(out_paths["combined"])


def run_pipeline(
    gpx_path: str | Path,
    dem_path: str | Path,
    stl_output_path: str | Path,
    config: GenerateConfig,
    backend: str = "python",
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


def estimate_relief_mm(gpx_path: str | Path, dem_path: str | Path, params: GenerateConfig) -> float:
    points_lonlat = load_gpx_points(gpx_path)
    with rasterio.open(dem_path) as ds:
        if ds.crs is None:
            raise ValueError("Il DEM non ha CRS definito.")

        to_dem = Transformer.from_crs("EPSG:4326", ds.crs, always_xy=True)
        x_dem, y_dem = to_dem.transform(points_lonlat[:, 0], points_lonlat[:, 1])
        points_dem = np.column_stack((x_dem, y_dem))

        minx, miny, maxx, maxy = _compute_bbox(points_dem, params.bbox_margin_ratio)
        window = rasterio.windows.from_bounds(minx, miny, maxx, maxy, transform=ds.transform)
        window = window.round_offsets().round_lengths()

        data = ds.read(1, window=window, masked=True)
        if data.size == 0:
            raise ValueError("Ritaglio DEM vuoto: controlla GPX e DEM.")

        dem = np.asarray(data.astype(np.float64).filled(np.nan), dtype=np.float64)
        finite = np.isfinite(dem)
        if not np.any(finite):
            raise ValueError("Ritaglio DEM privo di valori validi.")

        z_min = float(np.nanmin(dem))
        z_max = float(np.nanmax(dem))

        horiz_scale_mm_per_meter = _model_horizontal_scale_mm_per_meter(
            ds, window, params.model_width_mm, params.model_height_mm
        )

    return float((z_max - z_min) * horiz_scale_mm_per_meter * params.vertical_scale)
