from __future__ import annotations

import numpy as np
import trimesh


_TRACK_BASE_OFFSET_MM = -0.18
_TRACK_SMOOTH_WINDOW = 11


def _grid_index(row: int, col: int, cols: int) -> int:
    return row * cols + col


def _smooth_series(values: np.ndarray, window: int = 5) -> np.ndarray:
    if window <= 1 or values.size < 3:
        return values
    window = min(window, values.size)
    if window % 2 == 0:
        window -= 1
    if window < 3:
        return values
    pad = window // 2
    padded = np.pad(values, pad, mode="edge")
    kernel = np.ones(window, dtype=np.float64) / window
    return np.convolve(padded, kernel, mode="valid")


def build_terrain_mesh(
    x_mm: np.ndarray,
    y_mm: np.ndarray,
    z_mm: np.ndarray,
    base_thickness_mm: float,
) -> trimesh.Trimesh:
    rows, cols = z_mm.shape
    xx, yy = np.meshgrid(x_mm, y_mm)

    top_vertices = np.column_stack((xx.ravel(), yy.ravel(), z_mm.ravel()))
    bottom_vertices = np.column_stack((xx.ravel(), yy.ravel(), np.full(rows * cols, -base_thickness_mm)))
    vertices = np.vstack((top_vertices, bottom_vertices))

    faces: list[list[int]] = []

    # Top + bottom
    for r in range(rows - 1):
        for c in range(cols - 1):
            v00 = _grid_index(r, c, cols)
            v10 = _grid_index(r + 1, c, cols)
            v01 = _grid_index(r, c + 1, cols)
            v11 = _grid_index(r + 1, c + 1, cols)

            faces.append([v00, v10, v01])
            faces.append([v01, v10, v11])

            b00 = v00 + rows * cols
            b10 = v10 + rows * cols
            b01 = v01 + rows * cols
            b11 = v11 + rows * cols
            faces.append([b00, b01, b10])
            faces.append([b01, b11, b10])

    def add_wall(top_a: int, top_b: int) -> None:
        bot_a = top_a + rows * cols
        bot_b = top_b + rows * cols
        faces.append([top_a, top_b, bot_a])
        faces.append([top_b, bot_b, bot_a])

    for c in range(cols - 1):
        add_wall(_grid_index(0, c, cols), _grid_index(0, c + 1, cols))
        add_wall(_grid_index(rows - 1, c + 1, cols), _grid_index(rows - 1, c, cols))

    for r in range(rows - 1):
        add_wall(_grid_index(r + 1, 0, cols), _grid_index(r, 0, cols))
        add_wall(_grid_index(r, cols - 1, cols), _grid_index(r + 1, cols - 1, cols))

    mesh = trimesh.Trimesh(vertices=vertices, faces=np.asarray(faces, dtype=np.int64), process=False)
    return mesh


def sample_height_on_grid(x_mm: np.ndarray, y_mm: np.ndarray, z_mm: np.ndarray, px: float, py: float) -> float:
    x = np.clip(px, x_mm[0], x_mm[-1])
    y = np.clip(py, y_mm[0], y_mm[-1])

    ix = np.searchsorted(x_mm, x, side="right") - 1
    iy = np.searchsorted(y_mm, y, side="right") - 1
    ix = np.clip(ix, 0, len(x_mm) - 2)
    iy = np.clip(iy, 0, len(y_mm) - 2)

    x0, x1 = x_mm[ix], x_mm[ix + 1]
    y0, y1 = y_mm[iy], y_mm[iy + 1]

    tx = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
    ty = 0.0 if y1 == y0 else (y - y0) / (y1 - y0)

    z00 = z_mm[iy, ix]
    z10 = z_mm[iy, ix + 1]
    z01 = z_mm[iy + 1, ix]
    z11 = z_mm[iy + 1, ix + 1]

    z0 = z00 * (1 - tx) + z10 * tx
    z1 = z01 * (1 - tx) + z11 * tx
    return float(z0 * (1 - ty) + z1 * ty)


def _track_profile_offsets(
    track_width_mm: float,
    track_height_mm: float,
    top_radius_mm: float,
    arc_points: int = 5,
) -> list[tuple[float, float]]:
    width = max(track_width_mm, 0.1)
    height = max(track_height_mm, 0.1)
    half_base = width * 0.5
    shoulder_half = half_base * 0.82
    shoulder_height = height * 0.55
    radius = max(0.0, min(top_radius_mm, height, half_base))
    half_top = min(half_base * 0.55, max(radius, half_base * 0.35))
    if radius <= 1e-3:
        arc = [(-half_top, height), (half_top, height)]
    else:
        half_top = min(half_top, radius)
        arc_x = np.linspace(-half_top, half_top, num=max(3, arc_points))
        arc_z = height - radius + np.sqrt(np.clip(radius * radius - arc_x * arc_x, 0.0, None))
        arc = list(zip(arc_x, arc_z))
    profile: list[tuple[float, float]] = [(-half_base, 0.0), (-shoulder_half, shoulder_height)]
    profile.extend(arc)
    profile.append((shoulder_half, shoulder_height))
    profile.append((half_base, 0.0))
    return profile


def _safe_normal(vec: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vec)
    if norm < 1e-6:
        return np.array([1.0, 0.0], dtype=np.float64)
    return vec / norm


def build_track_mesh(
    track_xy_mm: np.ndarray,
    x_mm: np.ndarray,
    y_mm: np.ndarray,
    z_mm: np.ndarray,
    track_height_mm: float,
    track_width_mm: float = 1.2,
    top_radius_mm: float = 0.0,
    base_offset_mm: float = _TRACK_BASE_OFFSET_MM,
) -> trimesh.Trimesh:
    if len(track_xy_mm) < 2:
        return trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3), dtype=np.int64), process=False)

    z_samples = np.array(
        [sample_height_on_grid(x_mm, y_mm, z_mm, p[0], p[1]) for p in track_xy_mm],
        dtype=np.float64,
    )
    z_samples = _smooth_series(z_samples, window=_TRACK_SMOOTH_WINDOW) + base_offset_mm

    directions = np.diff(track_xy_mm, axis=0)
    directions = np.array([_safe_normal(vec) for vec in directions], dtype=np.float64)
    tangents: list[np.ndarray] = []
    for i in range(len(track_xy_mm)):
        if i == 0:
            tangent = directions[0]
        elif i == len(track_xy_mm) - 1:
            tangent = directions[-1]
        else:
            tangent = _safe_normal(directions[i - 1] + directions[i])
        tangents.append(tangent)
    tangents_np = np.asarray(tangents, dtype=np.float64)
    normals = np.column_stack((-tangents_np[:, 1], tangents_np[:, 0]))

    profile = _track_profile_offsets(track_width_mm, track_height_mm, top_radius_mm)
    profile_count = len(profile)
    vertices = np.zeros((len(track_xy_mm) * profile_count, 3), dtype=np.float64)
    for i, center in enumerate(track_xy_mm):
        normal = normals[i]
        base_z = z_samples[i]
        for j, (offset, height) in enumerate(profile):
            idx = i * profile_count + j
            vertices[idx] = np.array([center[0] + normal[0] * offset, center[1] + normal[1] * offset, base_z + height])

    faces: list[list[int]] = []
    for i in range(len(track_xy_mm) - 1):
        start = i * profile_count
        nxt = (i + 1) * profile_count
        for j in range(profile_count):
            j_next = (j + 1) % profile_count
            v0 = start + j
            v1 = start + j_next
            v2 = nxt + j
            v3 = nxt + j_next
            faces.append([v0, v2, v1])
            faces.append([v1, v2, v3])

    cap_start = vertices[:profile_count].mean(axis=0)
    cap_end = vertices[-profile_count:].mean(axis=0)
    cap_start_idx = len(vertices)
    cap_end_idx = cap_start_idx + 1
    vertices = np.vstack((vertices, cap_start, cap_end))
    for j in range(profile_count):
        j_next = (j + 1) % profile_count
        faces.append([cap_start_idx, j_next, j])
        end_base = (len(track_xy_mm) - 1) * profile_count
        faces.append([cap_end_idx, end_base + j, end_base + j_next])

    return trimesh.Trimesh(vertices=vertices, faces=np.asarray(faces, dtype=np.int64), process=False)


def build_line_layer_mesh(
    line_segments_xy_mm: list[np.ndarray],
    x_mm: np.ndarray,
    y_mm: np.ndarray,
    z_mm: np.ndarray,
    layer_height_mm: float,
    layer_width_mm: float,
    top_radius_mm: float = 0.0,
    base_offset_mm: float = _TRACK_BASE_OFFSET_MM,
) -> trimesh.Trimesh:
    meshes: list[trimesh.Trimesh] = []
    for segment in line_segments_xy_mm:
        if len(segment) < 2:
            continue
        mesh = build_track_mesh(
            track_xy_mm=segment,
            x_mm=x_mm,
            y_mm=y_mm,
            z_mm=z_mm,
            track_height_mm=layer_height_mm,
            track_width_mm=layer_width_mm,
            top_radius_mm=top_radius_mm,
            base_offset_mm=base_offset_mm,
        )
        if mesh.faces.shape[0] > 0:
            meshes.append(mesh)

    if not meshes:
        return trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3), dtype=np.int64), process=False)

    return trimesh.util.concatenate(meshes)



def _box_mesh(min_x: float, max_x: float, min_y: float, max_y: float, min_z: float, max_z: float) -> trimesh.Trimesh:
    size = np.array([max_x - min_x, max_y - min_y, max_z - min_z], dtype=np.float64)
    center = np.array([(min_x + max_x) * 0.5, (min_y + max_y) * 0.5, (min_z + max_z) * 0.5], dtype=np.float64)
    return trimesh.creation.box(extents=size, transform=trimesh.transformations.translation_matrix(center))


def build_rect_frame_mesh(
    model_width_mm: float,
    model_height_mm: float,
    frame_wall_mm: float,
    frame_height_mm: float,
    clearance_mm: float,
    base_thickness_mm: float,
) -> trimesh.Trimesh:
    inner_min_x = -clearance_mm
    inner_max_x = model_width_mm + clearance_mm
    inner_min_y = -clearance_mm
    inner_max_y = model_height_mm + clearance_mm

    outer_min_x = inner_min_x - frame_wall_mm
    outer_max_x = inner_max_x + frame_wall_mm
    outer_min_y = inner_min_y - frame_wall_mm
    outer_max_y = inner_max_y + frame_wall_mm

    min_z = -base_thickness_mm
    max_z = frame_height_mm

    parts = [
        _box_mesh(outer_min_x, outer_max_x, outer_min_y, inner_min_y, min_z, max_z),
        _box_mesh(outer_min_x, outer_max_x, inner_max_y, outer_max_y, min_z, max_z),
        _box_mesh(outer_min_x, inner_min_x, inner_min_y, inner_max_y, min_z, max_z),
        _box_mesh(inner_max_x, outer_max_x, inner_min_y, inner_max_y, min_z, max_z),
    ]

    return trimesh.util.concatenate(parts)
