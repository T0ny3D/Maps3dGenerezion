from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.warp import reproject
from pyproj import Geod, Transformer
import trimesh
from shapely.geometry import GeometryCollection, LineString, MultiLineString, box

from .gpx_loader import load_gpx_points
from .mesh_builder import build_line_layer_mesh, build_rect_frame_mesh, build_terrain_mesh
from .model_space import ModelSpace

_WGS84_GEOD = Geod(ellps="WGS84")
_RELIEF_DETAIL_BOOST_FINE = 0.85  # Unsharp-mask strength for micro detail.
_RELIEF_DETAIL_BOOST_COARSE = 0.45  # Medium-scale ridge emphasis.
_RELIEF_GAMMA = 0.78  # Gamma lift to improve relief readability.
_RELIEF_CONTRAST = 1.18  # S-curve contrast for stronger relief separation.
_RELIEF_CONTRAST_CENTER = 0.5
_RELIEF_CONTRAST_SCALE = 0.5
_RELIEF_SMOOTH_RADIUS_FINE = 1  # Box-filter radius for micro relief.
_RELIEF_SMOOTH_RADIUS_COARSE = 5  # Box-filter radius for macro relief.
_RELIEF_MAX_CELLS = 2_000_000
_TRACK_SIMPLIFY_TOL_MM = 0.65
_TRACK_RESAMPLE_MM = 0.9
_TRACK_MIN_LENGTH_MM = 3.0
_TRACK_MIN_WIDTH_MM = 1.5
_TRACK_MAX_WIDTH_MM = 3.2
_TRACK_CLEARANCE_MULTIPLIER = 2.0
_WATERWAY_TYPES = {"river", "canal"}  # Exclude small streams to keep only dominant waterways.
_GREEN_LANDUSE = {"forest", "meadow", "grass", "wood"}
_GREEN_LEISURE = {"park", "garden"}
_HIGHWAY_TYPES = {"motorway", "trunk", "primary"}
_WATERWAY_QUERY = "|".join(sorted(_WATERWAY_TYPES))
_GREEN_LANDUSE_QUERY = "|".join(sorted(_GREEN_LANDUSE))
_GREEN_LEISURE_QUERY = "|".join(sorted(_GREEN_LEISURE))
_HIGHWAY_QUERY = "|".join(sorted(_HIGHWAY_TYPES))
_WATER_SIMPLIFY_TOL_MM = 1.0
_WATER_RESAMPLE_MM = 2.4
_WATER_MIN_LENGTH_MM = 14.0
_GREEN_SIMPLIFY_TOL_MM = 1.05
_GREEN_RESAMPLE_MM = 2.6
_GREEN_MIN_LENGTH_MM = 12.0
_DETAIL_SIMPLIFY_TOL_MM = 1.4
_DETAIL_RESAMPLE_MM = 3.2
_DETAIL_MIN_LENGTH_MM = 18.0
_WATER_MAX_SEGMENTS = 6
_GREEN_MAX_SEGMENTS = 8
_DETAIL_MAX_SEGMENTS = 5
_WATER_MAX_TOTAL_RATIO = 1.6
_GREEN_MAX_TOTAL_RATIO = 1.35
_DETAIL_MAX_TOTAL_RATIO = 1.1
_WATER_MIN_SPAN_RATIO = 0.18
_GREEN_MIN_SPAN_RATIO = 0.12
_DETAIL_MIN_SPAN_RATIO = 0.2
_WATER_LAYER_HEIGHT_MM = 1.2
_WATER_LAYER_WIDTH_MM = 2.9
_GREEN_LAYER_HEIGHT_MM = 1.1
_GREEN_LAYER_WIDTH_MM = 2.4
_DETAIL_LAYER_HEIGHT_MM = 0.25
_DETAIL_LAYER_WIDTH_MM = 0.55
_OSM_SCORE_BASE = 0.65  # Dimensionless base weight for feature importance ranking.
_WATER_TOP_RADIUS_MAX_MM = 0.9
_WATER_TOP_RADIUS_RATIO = 0.4
_GREEN_TOP_RADIUS_MAX_MM = 0.7
_GREEN_TOP_RADIUS_RATIO = 0.38
_DETAIL_TOP_RADIUS_MAX_MM = 0.35
_DETAIL_TOP_RADIUS_RATIO = 0.4


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


def _extract_line_segments(geom: object) -> list[np.ndarray]:
    if isinstance(geom, LineString):
        coords = np.asarray(geom.coords, dtype=np.float64)
        return [coords] if len(coords) >= 2 else []
    segments: list[np.ndarray] = []
    if isinstance(geom, MultiLineString):
        for line in geom.geoms:
            coords = np.asarray(line.coords, dtype=np.float64)
            if len(coords) >= 2:
                segments.append(coords)
        return segments
    if isinstance(geom, GeometryCollection):
        for child in geom.geoms:
            segments.extend(_extract_line_segments(child))
        return segments
    return []


@dataclass
class GenerateConfig:
    model_width_mm: float = 120.0
    model_height_mm: float = 120.0
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


def _target_grid_shape(rows: int, cols: int, grid_res: int) -> tuple[int, int]:
    if grid_res <= 0:
        return rows, cols
    max_dim = max(rows, cols)
    if max_dim <= 0:
        return rows, cols
    scale = grid_res / max_dim
    target_rows = max(2, int(round(rows * scale)))
    target_cols = max(2, int(round(cols * scale)))
    return target_rows, target_cols


def _fill_dem_nans(dem: np.ndarray) -> np.ndarray:
    finite = np.isfinite(dem)
    if not np.any(finite):
        raise ValueError("Ritaglio DEM privo di valori validi.")
    min_elev = float(np.nanmin(dem))
    return np.where(np.isfinite(dem), dem, min_elev)


def _resample_dem_grid(
    dem: np.ndarray,
    src_transform: rasterio.Affine,
    src_crs: rasterio.crs.CRS,
    bounds: tuple[float, float, float, float],
    target_rows: int,
    target_cols: int,
) -> tuple[np.ndarray, rasterio.Affine]:
    dst = np.empty((target_rows, target_cols), dtype=np.float64)
    dst_transform = from_bounds(*bounds, width=target_cols, height=target_rows)
    reproject(
        source=dem,
        destination=dst,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=src_crs,
        resampling=Resampling.cubic,
        src_nodata=np.nan,
        dst_nodata=np.nan,
    )
    return dst, dst_transform


def _box_filter(values: np.ndarray, radius: int = 1) -> np.ndarray:
    if radius <= 0:
        return values
    if values.size > _RELIEF_MAX_CELLS:
        return values
    kernel = radius * 2 + 1
    padded = np.pad(values, radius, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, (kernel, kernel))
    return windows.mean(axis=(-1, -2))


def _relief_contrast(values: np.ndarray, strength: float) -> np.ndarray:
    if strength <= 0:
        return values
    centered = values - _RELIEF_CONTRAST_CENTER
    # Map tanh output from [-1, 1] back to [0, 1] to apply an S-curve that emphasizes mid-range
    # transitions while preserving peaks/valleys.
    adjusted = _RELIEF_CONTRAST_CENTER + np.tanh(centered * strength) * _RELIEF_CONTRAST_SCALE
    return np.clip(adjusted, 0.0, 1.0)


def _enhance_dem_relief(dem: np.ndarray) -> np.ndarray:
    min_elev = float(np.nanmin(dem))
    max_elev = float(np.nanmax(dem))
    span = max_elev - min_elev
    if span <= 1e-6:
        return dem
    norm = (dem - min_elev) / span
    smooth_fine = _box_filter(norm, radius=_RELIEF_SMOOTH_RADIUS_FINE)
    smooth_coarse = _box_filter(norm, radius=_RELIEF_SMOOTH_RADIUS_COARSE)
    detail_fine = norm - smooth_fine
    detail_coarse = smooth_fine - smooth_coarse
    boosted = norm + detail_fine * _RELIEF_DETAIL_BOOST_FINE + detail_coarse * _RELIEF_DETAIL_BOOST_COARSE
    boosted = np.clip(boosted, 0.0, 1.0)
    contrasted = _relief_contrast(boosted, _RELIEF_CONTRAST)
    adjusted = np.power(contrasted, _RELIEF_GAMMA)
    return adjusted * span + min_elev


def _prepare_dem_grid(
    ds: rasterio.io.DatasetReader,
    points_dem: np.ndarray,
    config: GenerateConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, rasterio.windows.Window, float]:
    minx, miny, maxx, maxy = _compute_bbox(points_dem, config.bbox_margin_ratio)
    window = rasterio.windows.from_bounds(minx, miny, maxx, maxy, transform=ds.transform)
    window = window.round_offsets().round_lengths()

    data = ds.read(1, window=window, masked=True)
    if data.size == 0:
        raise ValueError("Ritaglio DEM vuoto: controlla GPX e DEM.")

    dem = np.asarray(data.astype(np.float64).filled(np.nan), dtype=np.float64)
    if not np.any(np.isfinite(dem)):
        raise ValueError("Ritaglio DEM privo di valori validi.")

    win_t = ds.window_transform(window)
    bounds = rasterio.windows.bounds(window, ds.transform)
    rows, cols = dem.shape
    target_rows, target_cols = _target_grid_shape(rows, cols, config.grid_res)
    if (target_rows, target_cols) != (rows, cols):
        dem, win_t = _resample_dem_grid(dem, win_t, ds.crs, bounds, target_rows, target_cols)

    dem = _fill_dem_nans(dem)
    dem = _enhance_dem_relief(dem)
    min_elev = float(np.nanmin(dem))

    rows, cols = dem.shape
    x_coords = win_t.c + (np.arange(cols) + 0.5) * win_t.a
    y_coords = win_t.f + (np.arange(rows) + 0.5) * win_t.e
    return dem, x_coords, y_coords, window, min_elev


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
    return _extract_line_segments(clipped)


def _resample_polyline(points: np.ndarray, spacing_mm: float) -> np.ndarray:
    if len(points) < 2 or spacing_mm <= 0:
        return points
    line = LineString(points)
    length = line.length
    if length <= spacing_mm:
        return np.asarray(line.coords, dtype=np.float64)
    distances = np.arange(0.0, length, spacing_mm)
    if distances.size == 0 or distances[-1] < length:
        distances = np.append(distances, length)
    coords = [line.interpolate(dist).coords[0] for dist in distances]
    return np.asarray(coords, dtype=np.float64)


def _line_length(points: np.ndarray) -> float:
    if len(points) < 2:
        return 0.0
    diffs = np.diff(points, axis=0)
    return float(np.sum(np.linalg.norm(diffs, axis=1)))


def _normalize_line_segments(
    segments: list[np.ndarray],
    simplify_tolerance_mm: float,
    resample_spacing_mm: float,
    min_length_mm: float,
) -> list[np.ndarray]:
    normalized: list[np.ndarray] = []
    for segment in segments:
        if len(segment) < 2:
            continue
        line = LineString(segment)
        if simplify_tolerance_mm > 0:
            line = line.simplify(simplify_tolerance_mm, preserve_topology=False)
        if line.is_empty:
            continue
        for coords in _extract_line_segments(line):
            if _line_length(coords) < min_length_mm:
                continue
            coords = _resample_polyline(coords, resample_spacing_mm)
            if len(coords) >= 2 and _line_length(coords) >= min_length_mm:
                normalized.append(coords)
    return normalized


def _segment_bounds(points: np.ndarray) -> tuple[float, float, float, float]:
    min_xy = np.min(points, axis=0)
    max_xy = np.max(points, axis=0)
    return float(min_xy[0]), float(min_xy[1]), float(max_xy[0]), float(max_xy[1])


def _segment_span_ratio(points: np.ndarray, model_span_mm: float) -> float:
    if model_span_mm <= 0:
        return 0.0
    min_x, min_y, max_x, max_y = _segment_bounds(points)
    diag = float(np.hypot(max_x - min_x, max_y - min_y))
    return diag / model_span_mm


def _select_top_segments(
    segments: list[np.ndarray],
    model_width_mm: float,
    model_height_mm: float,
    max_segments: int,
    min_length_mm: float,
    max_total_length_mm: float,
    min_span_ratio: float,
) -> list[np.ndarray]:
    if not segments:
        return []
    model_span = max(model_width_mm, model_height_mm, 1e-6)
    scored: list[tuple[float, float, np.ndarray]] = []
    for segment in segments:
        length = _line_length(segment)
        if length < min_length_mm:
            continue
        span_ratio = _segment_span_ratio(segment, model_span)
        if span_ratio < min_span_ratio:
            continue
        # Favor longer segments that span more of the model for clearer hierarchy; the base term
        # keeps dominant features visible even when span_ratio is modest.
        score = length * (_OSM_SCORE_BASE + span_ratio)
        scored.append((score, length, segment))

    if not scored:
        longest = max(segments, key=_line_length)
        longest_length = _line_length(longest)
        return [longest] if longest_length > 0 else []

    scored.sort(key=lambda item: item[0], reverse=True)
    selected: list[np.ndarray] = []
    total_length = 0.0
    for _, length, segment in scored:
        if len(selected) >= max_segments:
            break
        if total_length + length > max_total_length_mm and selected:
            continue
        selected.append(segment)
        total_length += length
    return selected


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
  way["waterway"~"{_WATERWAY_QUERY}"]({s},{w},{n},{e});
  way["landuse"~"{_GREEN_LANDUSE_QUERY}"]({s},{w},{n},{e});
  way["leisure"~"{_GREEN_LEISURE_QUERY}"]({s},{w},{n},{e});
  way["highway"~"{_HIGHWAY_QUERY}"]({s},{w},{n},{e});
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
        waterway = tags.get("waterway")
        highway = tags.get("highway")
        if tags.get("natural") == "water" or waterway in _WATERWAY_TYPES:
            layers["water"].append(model_xy)
        elif tags.get("landuse") in _GREEN_LANDUSE or tags.get("leisure") in _GREEN_LEISURE:
            layers["green"].append(model_xy)
        elif highway in _HIGHWAY_TYPES:
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

        dem, x_coords, y_coords, window, min_elev = _prepare_dem_grid(ds, points_dem, config)

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
    clipped_track_segments = _normalize_line_segments(
        clipped_track_segments,
        simplify_tolerance_mm=_TRACK_SIMPLIFY_TOL_MM,
        resample_spacing_mm=_TRACK_RESAMPLE_MM,
        min_length_mm=_TRACK_MIN_LENGTH_MM,
    )
    raw_track_width_mm = max(
        0.0,
        config.groove_width_mm - _TRACK_CLEARANCE_MULTIPLIER * config.track_clearance_mm,
    )
    track_width_mm = max(_TRACK_MIN_WIDTH_MM, raw_track_width_mm)
    track_mesh = build_line_layer_mesh(
        line_segments_xy_mm=clipped_track_segments,
        x_mm=x_mm,
        y_mm=y_mm,
        z_mm=z_mm,
        layer_height_mm=config.track_height_mm,
        layer_width_mm=min(track_width_mm, _TRACK_MAX_WIDTH_MM),
        top_radius_mm=config.track_top_radius_mm,
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
    clipped_water_segments = _normalize_line_segments(
        clipped_water_segments,
        simplify_tolerance_mm=_WATER_SIMPLIFY_TOL_MM,
        resample_spacing_mm=_WATER_RESAMPLE_MM,
        min_length_mm=_WATER_MIN_LENGTH_MM,
    )
    clipped_green_segments = _normalize_line_segments(
        clipped_green_segments,
        simplify_tolerance_mm=_GREEN_SIMPLIFY_TOL_MM,
        resample_spacing_mm=_GREEN_RESAMPLE_MM,
        min_length_mm=_GREEN_MIN_LENGTH_MM,
    )
    clipped_detail_segments = _normalize_line_segments(
        clipped_detail_segments,
        simplify_tolerance_mm=_DETAIL_SIMPLIFY_TOL_MM,
        resample_spacing_mm=_DETAIL_RESAMPLE_MM,
        min_length_mm=_DETAIL_MIN_LENGTH_MM,
    )

    model_span_mm = max(config.model_width_mm, config.model_height_mm, 1e-6)
    clipped_water_segments = _select_top_segments(
        clipped_water_segments,
        model_width_mm=config.model_width_mm,
        model_height_mm=config.model_height_mm,
        max_segments=_WATER_MAX_SEGMENTS,
        min_length_mm=_WATER_MIN_LENGTH_MM,
        max_total_length_mm=model_span_mm * _WATER_MAX_TOTAL_RATIO,
        min_span_ratio=_WATER_MIN_SPAN_RATIO,
    )
    clipped_green_segments = _select_top_segments(
        clipped_green_segments,
        model_width_mm=config.model_width_mm,
        model_height_mm=config.model_height_mm,
        max_segments=_GREEN_MAX_SEGMENTS,
        min_length_mm=_GREEN_MIN_LENGTH_MM,
        max_total_length_mm=model_span_mm * _GREEN_MAX_TOTAL_RATIO,
        min_span_ratio=_GREEN_MIN_SPAN_RATIO,
    )
    clipped_detail_segments = _select_top_segments(
        clipped_detail_segments,
        model_width_mm=config.model_width_mm,
        model_height_mm=config.model_height_mm,
        max_segments=_DETAIL_MAX_SEGMENTS,
        min_length_mm=_DETAIL_MIN_LENGTH_MM,
        max_total_length_mm=model_span_mm * _DETAIL_MAX_TOTAL_RATIO,
        min_span_ratio=_DETAIL_MIN_SPAN_RATIO,
    )

    water_mesh = build_line_layer_mesh(
        line_segments_xy_mm=clipped_water_segments,
        x_mm=x_mm,
        y_mm=y_mm,
        z_mm=z_mm,
        layer_height_mm=_WATER_LAYER_HEIGHT_MM,
        layer_width_mm=_WATER_LAYER_WIDTH_MM,
        top_radius_mm=min(_WATER_TOP_RADIUS_MAX_MM, _WATER_LAYER_WIDTH_MM * _WATER_TOP_RADIUS_RATIO),
        base_offset_mm=0.0,
    )
    green_mesh = build_line_layer_mesh(
        line_segments_xy_mm=clipped_green_segments,
        x_mm=x_mm,
        y_mm=y_mm,
        z_mm=z_mm,
        layer_height_mm=_GREEN_LAYER_HEIGHT_MM,
        layer_width_mm=_GREEN_LAYER_WIDTH_MM,
        top_radius_mm=min(_GREEN_TOP_RADIUS_MAX_MM, _GREEN_LAYER_WIDTH_MM * _GREEN_TOP_RADIUS_RATIO),
        base_offset_mm=0.0,
    )
    detail_mesh = build_line_layer_mesh(
        line_segments_xy_mm=clipped_detail_segments,
        x_mm=x_mm,
        y_mm=y_mm,
        z_mm=z_mm,
        layer_height_mm=_DETAIL_LAYER_HEIGHT_MM,
        layer_width_mm=_DETAIL_LAYER_WIDTH_MM,
        top_radius_mm=min(_DETAIL_TOP_RADIUS_MAX_MM, _DETAIL_LAYER_WIDTH_MM * _DETAIL_TOP_RADIUS_RATIO),
        base_offset_mm=0.0,
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

        dem, _, _, window, _ = _prepare_dem_grid(ds, points_dem, params)
        z_min = float(np.nanmin(dem))
        z_max = float(np.nanmax(dem))

        horiz_scale_mm_per_meter = _model_horizontal_scale_mm_per_meter(
            ds, window, params.model_width_mm, params.model_height_mm
        )

    return float((z_max - z_min) * horiz_scale_mm_per_meter * params.vertical_scale)
