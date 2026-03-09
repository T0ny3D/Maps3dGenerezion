from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import bmesh
import bpy


def _stage_log(stage: str, message: str) -> None:
    print(f"[maps3d][stage] {stage}: {message}", flush=True)


def _safe_int(raw: object, default: int, min_value: int, max_value: int, label: str) -> int:
    try:
        value = int(raw)
    except Exception:
        value = default
    clamped = max(min_value, min(max_value, value))
    if clamped != value:
        _stage_log("guard", f"{label} clamped from {value} to {clamped}")
    return clamped


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def _enable_smooth_shading(obj: bpy.types.Object) -> None:
    if obj.type != "MESH" or obj.data is None:
        return
    for poly in obj.data.polygons:
        poly.use_smooth = True
    if hasattr(obj.data, "use_auto_smooth"):
        obj.data.use_auto_smooth = True
        obj.data.auto_smooth_angle = math.radians(40.0)


def _apply_boolean(base: bpy.types.Object, tool: bpy.types.Object, op: str) -> None:
    base_polys = len(base.data.polygons) if base.type == "MESH" and base.data else -1
    tool_polys = len(tool.data.polygons) if tool.type == "MESH" and tool.data else -1
    max_per_mesh = 900000
    max_total = 1400000
    if base_polys > max_per_mesh or tool_polys > max_per_mesh or (base_polys + tool_polys) > max_total:
        _stage_log(
            "boolean",
            f"skip op={op} base={base.name} tool={tool.name} base_polys={base_polys} tool_polys={tool_polys} reason=density_guard",
        )
        bpy.data.objects.remove(tool, do_unlink=True)
        return
    _stage_log("boolean", f"start op={op} base={base.name} tool={tool.name} base_polys={base_polys} tool_polys={tool_polys}")
    mod = base.modifiers.new(name=f"Bool_{op}", type="BOOLEAN")
    mod.operation = op
    mod.solver = "EXACT"
    mod.object = tool
    bpy.context.view_layer.objects.active = base
    bpy.ops.object.modifier_apply(modifier=mod.name)
    _stage_log("boolean", f"end op={op} base_polys={len(base.data.polygons) if base.type=='MESH' and base.data else -1}")
    bpy.data.objects.remove(tool, do_unlink=True)


def _debug_log(message: str) -> None:
    print(f"[maps3d][track_inlay] {message}", flush=True)


def _points_bbox(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    if not points:
        return 0.0, 0.0, 0.0, 0.0

def _fit_points_to_terrain(points: list[tuple[float, float]], size_x: float, size_y: float) -> tuple[list[tuple[float, float]], bool]:
    if len(points) < 2:
        return points, False

 main
    min_x = min(p[0] for p in points)
    max_x = max(p[0] for p in points)
    min_y = min(p[1] for p in points)
    max_y = max(p[1] for p in points)
    return min_x, max_x, min_y, max_y


def _fit_points_to_terrain(points: list[tuple[float, float]], size_x: float, size_y: float) -> tuple[list[tuple[float, float]], bool, str]:
    if len(points) < 2:
        return points, False, "insufficient_points"

    min_x, max_x, min_y, max_y = _points_bbox(points)
    span_x = max(1e-6, max_x - min_x)
    span_y = max(1e-6, max_y - min_y)
    terrain_span_x = max(1e-6, float(size_x))
    terrain_span_y = max(1e-6, float(size_y))
    span_overflow = span_x > (terrain_span_x * 1.01) or span_y > (terrain_span_y * 1.01)
    bounds_overflow = min_x < -0.5 or min_y < -0.5 or max_x > (terrain_span_x + 0.5) or max_y > (terrain_span_y + 0.5)
    need_fit = span_overflow or bounds_overflow
    reason = "span_overflow" if span_overflow else ("out_of_bounds" if bounds_overflow else "within_bounds")

    if not need_fit:
        return points, False, reason

    sx = terrain_span_x / span_x
    sy = terrain_span_y / span_y
    scale = min(sx, sy)
    src_cx = (min_x + max_x) * 0.5
    src_cy = (min_y + max_y) * 0.5
    dst_cx = terrain_span_x * 0.5
    dst_cy = terrain_span_y * 0.5
    out = [((x - src_cx) * scale + dst_cx, (y - src_cy) * scale + dst_cy) for (x, y) in points]
    return out, True, reason

    span_x = max(1e-6, max_x - min_x)
    span_y = max(1e-6, max_y - min_y)

    overflow = span_x > (size_x * 1.02) or span_y > (size_y * 1.02)
    if not overflow:
        return points, False

    sx = size_x / span_x
    sy = size_y / span_y
    scale = min(sx, sy)
    src_cx = (min_x + max_x) * 0.5
    src_cy = (min_y + max_y) * 0.5
    dst_cx = size_x * 0.5
    dst_cy = size_y * 0.5
    out = [((x - src_cx) * scale + dst_cx, (y - src_cy) * scale + dst_cy) for (x, y) in points]
    _debug_log(
        f"track footprint normalized src_span=({span_x:.3f},{span_y:.3f}) terrain_span=({size_x:.3f},{size_y:.3f}) scale={scale:.6f}"
    )
    return out, True
 main


def _simplify_mesh_for_boolean(mesh_obj: bpy.types.Object, target_polys: int) -> bool:
    if mesh_obj.type != "MESH" or mesh_obj.data is None:
        return False
    current = len(mesh_obj.data.polygons)
    target = max(50000, int(target_polys))
    if current <= target:
        return False
    ratio = max(0.02, min(1.0, target / float(current)))
    dec = mesh_obj.modifiers.new(name="BoolPreDecimate", type="DECIMATE")
    dec.ratio = ratio
    dec.use_collapse_triangulate = True
    _set_object_active_selected(mesh_obj)
    bpy.ops.object.modifier_apply(modifier=dec.name)
    after = len(mesh_obj.data.polygons)
    _debug_log(f"boolean simplify object={mesh_obj.name} polys={current}->{after} target={target} ratio={ratio:.5f}")
    return after < current


def _resample_track(points: list[list[float]], step_mm: float = 1.0, max_points: int = 50000) -> list[tuple[float, float]]:
    if len(points) < 2:
        return []

    src: list[tuple[float, float]] = []
    for p in points:
        if len(p) < 2:
            continue
        x = float(p[0])
        y = float(p[1])
        if not math.isfinite(x) or not math.isfinite(y):
            continue
        src.append((x, y))
    if len(src) < 2:
        return []

    total_len = 0.0
    for i in range(1, len(src)):
        total_len += math.hypot(src[i][0] - src[i - 1][0], src[i][1] - src[i - 1][1])

    safe_step = max(0.001, float(step_mm))
    max_points = max(1000, int(max_points))
    adaptive_step = max(safe_step, total_len / max_points) if total_len > 0.0 else safe_step

    out: list[tuple[float, float]] = [src[0]]
    for i in range(1, len(src)):
        x0, y0 = src[i - 1]
        x1, y1 = src[i]
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg < 1e-6:
            continue
        pieces = max(1, int(math.ceil(seg / adaptive_step)))
        for j in range(1, pieces + 1):
            t = j / pieces
            out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))

    if len(out) > max_points:
        stride = int(math.ceil(len(out) / max_points))
        out = out[::stride]
        if out[-1] != src[-1]:
            out.append(src[-1])

    _debug_log(
        f"resample src_points={len(src)} total_len_mm={total_len:.3f} step_mm={adaptive_step:.6f} out_points={len(out)} cap={max_points}"
    )
    return out if len(out) >= 2 else src


def _apply_rim_flatten(terrain: bpy.types.Object, size_x: float, size_y: float, rim_mm: float) -> None:
    if rim_mm <= 0.0:
        return
    mesh = terrain.data
    for v in mesh.vertices:
        d = min(v.co.x, size_x - v.co.x, v.co.y, size_y - v.co.y)
        if d <= 0:
            v.co.z = 0.0
        elif d < rim_mm and v.co.z > 0.0:
            v.co.z *= d / rim_mm
    mesh.update()


def _create_terrain(job: dict) -> bpy.types.Object:
    size_x = float(job["size_mm_x"])
    size_y = float(job["size_mm_y"])
    base_mm = float(job["base_mm"])
    grid_res = _safe_int(job.get("grid_res", 400), default=400, min_value=2, max_value=1600, label="grid_res")
    est_vertices = grid_res * grid_res
    _stage_log("terrain", f"begin size=({size_x:.3f},{size_y:.3f}) base_mm={base_mm:.3f} grid_res={grid_res} est_vertices={est_vertices}")

    bpy.ops.mesh.primitive_grid_add(x_subdivisions=grid_res, y_subdivisions=grid_res, size=1.0, location=(size_x / 2.0, size_y / 2.0, 0.0))
    terrain = bpy.context.active_object
    terrain.name = "Terrain"
    terrain.scale = (size_x / 2.0, size_y / 2.0, 1.0)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    _stage_log("terrain", f"loading heightmap path={job['heightmap_path']}")
    image = bpy.data.images.load(job["heightmap_path"])
    _stage_log("terrain", f"heightmap size={image.size[0]}x{image.size[1]}")
    tex = bpy.data.textures.new("HeightmapTex", type="IMAGE")
    tex.image = image

    displace = terrain.modifiers.new(name="Displace", type="DISPLACE")
    displace.texture = tex
    displace.texture_coords = "UV"
    displace.strength = float(job.get("z_scale", 1.0)) * float(job.get("z_range_mm", 0.0))
    displace.mid_level = 0.0

    _stage_log("terrain", f"apply displace strength={displace.strength:.6f}")
    bpy.context.view_layer.objects.active = terrain
    bpy.ops.object.modifier_apply(modifier=displace.name)
    _apply_rim_flatten(terrain, size_x, size_y, float(job.get("rim_mm", 3.0)))

    _stage_log("terrain", f"post-displace verts={len(terrain.data.vertices)} faces={len(terrain.data.polygons)}")
    solidify = terrain.modifiers.new(name="Solidify", type="SOLIDIFY")
    solidify.thickness = base_mm
    solidify.offset = -1.0
    bpy.ops.object.modifier_apply(modifier=solidify.name)

    _enable_smooth_shading(terrain)
    _stage_log("terrain", f"end verts={len(terrain.data.vertices)} edges={len(terrain.data.edges)} polys={len(terrain.data.polygons)} dims=({terrain.dimensions.x:.3f},{terrain.dimensions.y:.3f},{terrain.dimensions.z:.3f})")
    return terrain


def _curve_from_points(points: list[tuple[float, float]], name: str) -> bpy.types.Object:
    cdata = bpy.data.curves.new(f"{name}Data", type="CURVE")
    cdata.dimensions = "3D"
    cdata.resolution_u = 1 if len(points) > 2000 else 6
    spline = cdata.splines.new(type="POLY")
    spline.points.add(len(points) - 1)
    for i, (x, y) in enumerate(points):
        spline.points[i].co = (x, y, 0.0, 1.0)
    cobj = bpy.data.objects.new(name, cdata)
    bpy.context.collection.objects.link(cobj)
    _debug_log(f"curve name={name} points={len(points)} bevel_depth={float(cdata.bevel_depth):.4f} extrude={float(cdata.extrude):.4f} resolution_u={cdata.resolution_u}")
    return cobj


def _set_object_active_selected(obj: bpy.types.Object) -> None:
    view_layer = bpy.context.view_layer
    for candidate in view_layer.objects:
        candidate.select_set(False)
    obj.hide_set(False)
    obj.hide_viewport = False
    obj.select_set(True)
    view_layer.objects.active = obj
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def _curve_to_mesh(curve_obj: bpy.types.Object, name: str) -> bpy.types.Object:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_obj = curve_obj.evaluated_get(depsgraph)
    try:
        mesh_data = bpy.data.meshes.new_from_object(eval_obj, preserve_all_data_layers=True, depsgraph=depsgraph)
    except TypeError:
        mesh_data = bpy.data.meshes.new_from_object(eval_obj, depsgraph=depsgraph)

    mesh_obj = bpy.data.objects.new(name, mesh_data)
    mesh_obj.matrix_world = curve_obj.matrix_world.copy()

    linked = False
    for collection in curve_obj.users_collection:
        collection.objects.link(mesh_obj)
        linked = True
    if not linked:
        bpy.context.collection.objects.link(mesh_obj)

    bpy.data.objects.remove(curve_obj, do_unlink=True)
    _set_object_active_selected(mesh_obj)




    _debug_log(
        f"mesh name={name} verts={len(mesh_obj.data.vertices)} edges={len(mesh_obj.data.edges)} polys={len(mesh_obj.data.polygons)} "
        f"dims=({mesh_obj.dimensions.x:.3f},{mesh_obj.dimensions.y:.3f},{mesh_obj.dimensions.z:.3f})"
    )




    return mesh_obj


def _create_track_inlay(job: dict, terrain_top: bpy.types.Object) -> tuple[bpy.types.Object | None, bpy.types.Object | None]:
    if not bool(job.get("track_inlay_enabled", True)):
        return None, None

    size_x = abs(float(job.get("size_mm_x", 0.0)))
    size_y = abs(float(job.get("size_mm_y", 0.0)))
    perimeter_mm = max(1.0, 2.0 * (size_x + size_y))
    max_track_points = min(12000, max(1200, int(perimeter_mm * 2.0)))

    raw_track_points = job.get("track_points_mm", [])
    points = _resample_track(raw_track_points, 1.0, max_points=max_track_points)
    terrain_span_x = max(0.0, float(terrain_top.dimensions.x))
    terrain_span_y = max(0.0, float(terrain_top.dimensions.y))
    raw_min_x, raw_max_x, raw_min_y, raw_max_y = _points_bbox(points)
    points, normalized, fit_reason = _fit_points_to_terrain(points, terrain_span_x, terrain_span_y)
    fit_min_x, fit_max_x, fit_min_y, fit_max_y = _points_bbox(points)
    _debug_log(
        f"fit_check raw_bbox=({raw_min_x:.3f},{raw_max_x:.3f},{raw_min_y:.3f},{raw_max_y:.3f}) fitted_bbox=({fit_min_x:.3f},{fit_max_x:.3f},{fit_min_y:.3f},{fit_max_y:.3f}) terrain_span=({terrain_span_x:.3f},{terrain_span_y:.3f}) applied={normalized} reason={fit_reason}"
    )

    points, normalized = _fit_points_to_terrain(points, size_x, size_y)
 main
    if len(points) < 2:
        _debug_log("track inlay skipped: not enough valid track points")
        return None, None

    groove_width = max(0.2, float(job.get("groove_width_mm", 2.6)))
    groove_depth = max(0.05, float(job.get("groove_depth_mm", 1.6)))
    groove_chamfer = max(0.0, float(job.get("groove_chamfer_mm", 0.4)))
    clearance = max(0.0, float(job.get("track_clearance_mm", 0.2)))
    relief = max(0.0, float(job.get("track_relief_mm", 0.6)))
    top_radius = max(0.0, float(job.get("track_top_radius_mm", 0.8)))

    track_width = max(0.4, groove_width - 2.0 * clearance)
    total_h = groove_depth + relief

    _debug_log(
        f"input raw_points={len(raw_track_points)} points={len(points)} terrain_dims=({terrain_top.dimensions.x:.3f},{terrain_top.dimensions.y:.3f},{terrain_top.dimensions.z:.3f}) "
        f"groove_width={groove_width:.3f} groove_depth={groove_depth:.3f} track_width={track_width:.3f} total_h={total_h:.3f} normalized={normalized}"
    )


    _stage_log("track", f"creating groove curve points={len(points)} groove_width={groove_width:.3f} groove_depth={groove_depth:.3f}")

    groove_curve = _curve_from_points(points, "GrooveCurve")
    sw = groove_curve.modifiers.new(name="GrooveSW", type="SHRINKWRAP")
    sw.target = terrain_top
    sw.wrap_method = "PROJECT"
    sw.use_positive_direction = True
    sw.use_negative_direction = True
    sw.offset = 0.2
    groove_curve.data.bevel_depth = groove_width / 2.0
    groove_curve.data.fill_mode = "FULL"
    groove_curve.data.extrude = groove_depth

    _stage_log("track", "before groove curve->mesh")
    _set_object_active_selected(groove_curve)
    groove_mesh = _curve_to_mesh(groove_curve, "GrooveCurve")


    edge_count = len(groove_mesh.data.edges)
    if edge_count > 250000:
        _debug_log(f"groove bevel skipped: excessive edge_count={edge_count}")
    else:
        bm = bmesh.new()
        bm.from_mesh(groove_mesh.data)
        bmesh.ops.bevel(
            bm,
            geom=list(bm.edges),
            offset=max(0.05, min(groove_chamfer, groove_width * 0.2)),
            segments=1,
            profile=0.5,
            affect="EDGES",
            clamp_overlap=True,
        )
        bm.to_mesh(groove_mesh.data)
        bm.free()


    _stage_log("track", f"creating track curve points={len(points)} track_width={track_width:.3f} total_h={total_h:.3f}")

    track_curve = _curve_from_points(points, "TrackInlayCurve")
    sw2 = track_curve.modifiers.new(name="TrackSW", type="SHRINKWRAP")
    sw2.target = terrain_top
    sw2.wrap_method = "PROJECT"
    sw2.use_positive_direction = True
    sw2.use_negative_direction = True
    sw2.offset = 0.25
    track_curve.data.bevel_depth = track_width / 2.0
    track_curve.data.fill_mode = "FULL"
    track_curve.data.extrude = total_h

    _stage_log("track", "before track curve->mesh")

    _set_object_active_selected(track_curve)
    track_mesh = _curve_to_mesh(track_curve, "TrackInlayCurve")

    bev = track_mesh.modifiers.new(name="TopRound", type="BEVEL")
    bev.width = max(0.05, min(top_radius, track_width * 0.45))
    bev.segments = 2
    bev.limit_method = "ANGLE"


    _debug_log(
        f"top bevel pre-apply verts={len(track_mesh.data.vertices)} edges={len(track_mesh.data.edges)} polys={len(track_mesh.data.polygons)} width={bev.width:.4f} segments={bev.segments}"
    )


    _set_object_active_selected(track_mesh)
    bpy.ops.object.modifier_apply(modifier=bev.name)
    _debug_log(
        f"top bevel post-apply verts={len(track_mesh.data.vertices)} edges={len(track_mesh.data.edges)} polys={len(track_mesh.data.polygons)}"
    )

    _enable_smooth_shading(track_mesh)
    return groove_mesh, track_mesh


def _make_layer_from_curves(curves: list[bpy.types.Object], terrain: bpy.types.Object, thickness: float, name: str) -> bpy.types.Object | None:
    if not curves:
        return None
    meshes: list[bpy.types.Object] = []
    for c in curves:
        sw = c.modifiers.new(name="LayerSW", type="SHRINKWRAP")
        sw.target = terrain
        sw.wrap_method = "PROJECT"
        sw.use_positive_direction = True
        sw.use_negative_direction = True
        sw.offset = 0.25
        bpy.context.view_layer.objects.active = c
        bpy.ops.object.convert(target="MESH")
        m = bpy.context.active_object
        solid = m.modifiers.new(name="Solid", type="SOLIDIFY")
        solid.thickness = max(0.4, thickness)
        solid.offset = 0.0
        bpy.ops.object.modifier_apply(modifier=solid.name)
        meshes.append(m)

    base = meshes[0]
    for m in meshes[1:]:
        _apply_boolean(base, m, "UNION")
    base.name = name
    _enable_smooth_shading(base)
    return base


def _build_ams_layers(job: dict, terrain_top: bpy.types.Object) -> tuple[bpy.types.Object | None, bpy.types.Object | None, bpy.types.Object | None]:
    if not bool(job.get("ams_enabled", True)):
        return None, None, None
    sx = float(job["size_mm_x"])
    sy = float(job["size_mm_y"])

    def _curves_from_lines(lines: list[list[list[float]]], prefix: str, width: float) -> list[bpy.types.Object]:
        out: list[bpy.types.Object] = []
        for i, line in enumerate(lines):
            pts = [(float(p[0]), float(p[1])) for p in line if len(p) >= 2]
            if len(pts) < 2:
                continue
            c = _curve_from_points(pts, f"{prefix}{i}")
            c.data.bevel_depth = width
            c.data.extrude = 0.05
            out.append(c)
        return out

    water_curves = _curves_from_lines(job.get("osm_water_lines_mm", []), "WaterCurve", 1.4)
    green_curves = _curves_from_lines(job.get("osm_green_lines_mm", []), "GreenCurve", 1.8)
    detail_curves = _curves_from_lines(job.get("osm_detail_lines_mm", []), "DetailCurve", 0.45)

    if not water_curves:
        water_curves = [_curve_from_points([(0.1 * sx, 0.5 * sy), (0.9 * sx, 0.5 * sy)], "WaterFallback")]
        water_curves[0].data.bevel_depth = 1.4
    if not green_curves:
        green_curves = [_curve_from_points([(0.2 * sx, 0.2 * sy), (0.4 * sx, 0.35 * sy), (0.25 * sx, 0.6 * sy)], "GreenFallback")]
        green_curves[0].data.bevel_depth = 1.8
    if not detail_curves:
        for i in range(1, 6):
            y = (i / 6.0) * sy
            c = _curve_from_points([(0.08 * sx, y), (0.92 * sx, y + 2.0 * math.sin(i))], f"DetailFallback{i}")
            c.data.bevel_depth = 0.45
            detail_curves.append(c)

    water = _make_layer_from_curves(water_curves, terrain_top, thickness=0.8, name="WaterLayer")
    green = _make_layer_from_curves(green_curves, terrain_top, thickness=0.8, name="GreenLayer")
    detail = _make_layer_from_curves(detail_curves, terrain_top, thickness=0.6, name="DetailLayer")
    return water, green, detail


def _add_finger_notches(frame: bpy.types.Object, size_x: float, radius: float, z_level: float) -> None:
    if radius <= 0.0:
        return
    for x_pos in (size_x * 0.35, size_x * 0.65):
        bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=radius * 2.5, location=(x_pos, 0.0, z_level), rotation=(math.radians(90), 0.0, 0.0))
        _apply_boolean(frame, bpy.context.active_object, "DIFFERENCE")


def _create_frame(job: dict) -> bpy.types.Object:
    size_x = float(job["size_mm_x"])
    size_y = float(job["size_mm_y"])
    wall = float(job["frame_wall_mm"])
    frame_h = float(job["frame_height_mm"])
    clearance = float(job["clearance_mm"])
    recess_mm = float(job.get("recess_mm", 1.5))
    lip_depth = float(job["lip_depth_mm"])

    outer_x = size_x + 2.0 * wall
    outer_y = size_y + 2.0 * wall
    bpy.ops.mesh.primitive_cube_add(location=(size_x / 2.0, size_y / 2.0, frame_h / 2.0))
    frame = bpy.context.active_object
    frame.scale = (outer_x / 2.0, outer_y / 2.0, frame_h / 2.0)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    top_big_depth = max(0.2, min(recess_mm, frame_h - 0.5))
    seat_depth = max(0.2, min(lip_depth, frame_h - top_big_depth - 0.2))

    bpy.ops.mesh.primitive_cube_add(location=(size_x / 2.0, size_y / 2.0, frame_h - top_big_depth / 2.0))
    top_cut = bpy.context.active_object
    top_cut.scale = ((size_x + clearance) / 2.0, (size_y + clearance) / 2.0, top_big_depth / 2.0 + 1.0)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    _apply_boolean(frame, top_cut, "DIFFERENCE")

    bpy.ops.mesh.primitive_cube_add(location=(size_x / 2.0, size_y / 2.0, frame_h - top_big_depth - seat_depth / 2.0))
    seat_cut = bpy.context.active_object
    seat_cut.scale = (max(1.0, size_x - 2 * clearance) / 2.0, max(1.0, size_y - 2 * clearance) / 2.0, seat_depth / 2.0 + 1.0)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    _apply_boolean(frame, seat_cut, "DIFFERENCE")

    _add_finger_notches(frame, size_x, float(job.get("finger_notch_radius_mm", 7.0)), frame_h - recess_mm)
    _enable_smooth_shading(frame)
    frame.name = "Frame"
    return frame


def _make_test_map(map_obj: bpy.types.Object, test_size: float, size_x: float, size_y: float) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(location=(size_x / 2.0, size_y / 2.0, 0.0))
    cutter = bpy.context.active_object
    cutter.scale = (test_size / 2.0, test_size / 2.0, 1000.0)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    _apply_boolean(map_obj, cutter, "INTERSECT")
    return map_obj


def _create_test_frame_corner(job: dict) -> bpy.types.Object:
    frame = _create_frame(job)
    test_size = float(job.get("test_size_mm", 40.0))
    sx = float(job["size_mm_x"])
    sy = float(job["size_mm_y"])
    fh = float(job["frame_height_mm"])
    wx = float(job["frame_wall_mm"])

    bpy.ops.mesh.primitive_cube_add(location=(sx + wx - test_size / 2.0, sy + wx - test_size / 2.0, fh / 2.0))
    cutter = bpy.context.active_object
    cutter.scale = (test_size / 2.0, test_size / 2.0, fh)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    _apply_boolean(frame, cutter, "INTERSECT")
    return frame


def _export_stl(obj: bpy.types.Object | None, path: Path) -> None:
    if obj is None:
        _stage_log("export", f"skip none object -> {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.export_mesh.stl(filepath=str(path), use_selection=True)


def main() -> None:
    _stage_log("startup", f"argv={sys.argv}")
    if "--" not in sys.argv:
        raise RuntimeError("Percorso job.json mancante")

    job_path = Path(sys.argv[sys.argv.index("--") + 1])
    _stage_log("job", f"loading job json from {job_path}")
    raw_job = job_path.read_text(encoding="utf-8")
    job = json.loads(raw_job)
    _stage_log("job", f"job loaded keys={len(job.keys())} track_points={len(job.get('track_points_mm', []))} size=({job.get('size_mm_x')},{job.get('size_mm_y')})")
    out_base = Path(job.get("out_base_stl_path", "base_brown.stl"))
    out_water = Path(job.get("out_water_stl_path", "water.stl"))
    out_green = Path(job.get("out_green_stl_path", "green.stl"))
    out_detail = Path(job.get("out_detail_stl_path", "detail.stl"))
    out_track = Path(job.get("out_track_inlay_stl_path", "track_inlay_red.stl"))
    out_frame = Path(job.get("out_frame_stl_path", "frame.stl"))

    bpy.context.scene.unit_settings.system = "NONE"
    _stage_log("scene", "clearing scene")
    _clear_scene()

    _stage_log("terrain", "before terrain creation")
    base = _create_terrain(job)
    _stage_log("terrain", f"after terrain creation base={base.name} polys={len(base.data.polygons)}")
    terrain_for_layers = base.copy()
    terrain_for_layers.data = base.data.copy()
    bpy.context.collection.objects.link(terrain_for_layers)

    _stage_log("track", "before track inlay creation")
    groove, track_inlay = _create_track_inlay(job, terrain_for_layers)
    _stage_log("track", f"after track inlay creation groove={groove is not None} track={track_inlay is not None}")
    if groove is not None:
        terrain_xy_guard = 1.03
        track_dx = track_inlay.dimensions.x if track_inlay is not None else 0.0
        track_dy = track_inlay.dimensions.y if track_inlay is not None else 0.0
        track_dz = track_inlay.dimensions.z if track_inlay is not None else 0.0
        groove_too_wide = groove.dimensions.x > (base.dimensions.x * terrain_xy_guard) or groove.dimensions.y > (base.dimensions.y * terrain_xy_guard)
        track_too_wide = track_inlay is not None and (track_dx > (base.dimensions.x * terrain_xy_guard) or track_dy > (base.dimensions.y * terrain_xy_guard))
        if groove_too_wide or track_too_wide:
            _stage_log(
                "track",
                f"pre-boolean base_dims=({base.dimensions.x:.3f},{base.dimensions.y:.3f},{base.dimensions.z:.3f}) groove_dims=({groove.dimensions.x:.3f},{groove.dimensions.y:.3f},{groove.dimensions.z:.3f}) track_dims=({track_dx:.3f},{track_dy:.3f},{track_dz:.3f}) base_polys={len(base.data.polygons)} groove_polys={len(groove.data.polygons)} simplified=False skip_boolean=True reason=xy_oversize_after_fit",
            )
            bpy.data.objects.remove(groove, do_unlink=True)
        else:
            simplified = _simplify_mesh_for_boolean(groove, target_polys=280000)
            _stage_log(
                "track",
                f"pre-boolean base_dims=({base.dimensions.x:.3f},{base.dimensions.y:.3f},{base.dimensions.z:.3f}) groove_dims=({groove.dimensions.x:.3f},{groove.dimensions.y:.3f},{groove.dimensions.z:.3f}) track_dims=({track_dx:.3f},{track_dy:.3f},{track_dz:.3f}) base_polys={len(base.data.polygons)} groove_polys={len(groove.data.polygons)} simplified={simplified} skip_boolean=False reason=ok",
            )
            _apply_boolean(base, groove, "DIFFERENCE")


        simplified = _simplify_mesh_for_boolean(groove, target_polys=280000)
        _stage_log(
            "track",
            f"pre-boolean base_dims=({base.dimensions.x:.3f},{base.dimensions.y:.3f},{base.dimensions.z:.3f}) groove_dims=({groove.dimensions.x:.3f},{groove.dimensions.y:.3f},{groove.dimensions.z:.3f}) base_polys={len(base.data.polygons)} groove_polys={len(groove.data.polygons)} simplified={simplified}",
        )
        _apply_boolean(base, groove, "DIFFERENCE")
 main

    _stage_log("ams", "before AMS layer creation")
    water, green, detail = _build_ams_layers(job, terrain_for_layers)
    _stage_log("ams", f"after AMS layer creation water={water is not None} green={green is not None} detail={detail is not None}")

    if bool(job.get("test_mode", False)):
        ts = float(job.get("test_size_mm", 40.0))
        sx = float(job["size_mm_x"])
        sy = float(job["size_mm_y"])
        base = _make_test_map(base, ts, sx, sy)
        if water is not None:
            water = _make_test_map(water, ts, sx, sy)
        if green is not None:
            green = _make_test_map(green, ts, sx, sy)
        if detail is not None:
            detail = _make_test_map(detail, ts, sx, sy)
        if track_inlay is not None:
            track_inlay = _make_test_map(track_inlay, ts, sx, sy)

    _stage_log("export", f"exporting base -> {out_base}")
    _export_stl(base, out_base)
    _stage_log("export", f"exporting water -> {out_water}")
    _export_stl(water, out_water)
    _stage_log("export", f"exporting green -> {out_green}")
    _export_stl(green, out_green)
    _stage_log("export", f"exporting detail -> {out_detail}")
    _export_stl(detail, out_detail)
    _stage_log("export", f"exporting track -> {out_track}")
    _export_stl(track_inlay, out_track)

    if bool(job.get("separate_frame", True)):
        _stage_log("frame", "before frame creation")
        frame_obj = _create_test_frame_corner(job) if bool(job.get("test_mode", False)) else _create_frame(job)
        _stage_log("frame", f"after frame creation polys={len(frame_obj.data.polygons) if frame_obj and frame_obj.type=='MESH' else -1}")
        _stage_log("export", f"exporting frame -> {out_frame}")
        _export_stl(frame_obj, out_frame)


if __name__ == "__main__":
    _stage_log("startup", "blender_script module entry")
    main()
