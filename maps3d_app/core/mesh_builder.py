from __future__ import annotations

import numpy as np
import trimesh


def _grid_index(row: int, col: int, cols: int) -> int:
    return row * cols + col


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


def build_track_mesh(
    track_xy_mm: np.ndarray,
    x_mm: np.ndarray,
    y_mm: np.ndarray,
    z_mm: np.ndarray,
    track_height_mm: float,
    track_width_mm: float = 1.2,
) -> trimesh.Trimesh:
    vertices: list[np.ndarray] = []
    faces: list[list[int]] = []

    for i in range(len(track_xy_mm) - 1):
        p0 = track_xy_mm[i]
        p1 = track_xy_mm[i + 1]
        v = p1 - p0
        length = np.linalg.norm(v)
        if length < 1e-6:
            continue

        direction = v / length
        normal = np.array([-direction[1], direction[0]])
        offset = normal * (track_width_mm / 2.0)

        z0 = sample_height_on_grid(x_mm, y_mm, z_mm, p0[0], p0[1]) + 0.05
        z1 = sample_height_on_grid(x_mm, y_mm, z_mm, p1[0], p1[1]) + 0.05

        base0_l = np.array([p0[0] - offset[0], p0[1] - offset[1], z0])
        base0_r = np.array([p0[0] + offset[0], p0[1] + offset[1], z0])
        base1_l = np.array([p1[0] - offset[0], p1[1] - offset[1], z1])
        base1_r = np.array([p1[0] + offset[0], p1[1] + offset[1], z1])

        top0_l = base0_l + np.array([0.0, 0.0, track_height_mm])
        top0_r = base0_r + np.array([0.0, 0.0, track_height_mm])
        top1_l = base1_l + np.array([0.0, 0.0, track_height_mm])
        top1_r = base1_r + np.array([0.0, 0.0, track_height_mm])

        prism = [base0_l, base0_r, base1_l, base1_r, top0_l, top0_r, top1_l, top1_r]
        start = len(vertices)
        vertices.extend(prism)

        local_faces = [
            [0, 2, 1], [1, 2, 3],
            [4, 5, 6], [5, 7, 6],
            [0, 1, 4], [1, 5, 4],
            [2, 6, 3], [3, 6, 7],
            [1, 3, 5], [3, 7, 5],
            [0, 4, 2], [2, 4, 6],
        ]
        for face in local_faces:
            faces.append([start + idx for idx in face])

    if not vertices:
        return trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3), dtype=np.int64), process=False)

    return trimesh.Trimesh(vertices=np.asarray(vertices), faces=np.asarray(faces, dtype=np.int64), process=False)
