from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import bmesh
import bpy


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
    mod = base.modifiers.new(name=f"Bool_{op}", type="BOOLEAN")
    mod.operation = op
    mod.solver = "EXACT"
    mod.object = tool
    bpy.context.view_layer.objects.active = base
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(tool, do_unlink=True)


def _resample_track(points: list[list[float]], step_mm: float = 1.0) -> list[tuple[float, float]]:
    if len(points) < 2:
        return []
    src = [(float(p[0]), float(p[1])) for p in points]
    out: list[tuple[float, float]] = [src[0]]
    for i in range(1, len(src)):
        x0, y0 = src[i - 1]
        x1, y1 = src[i]
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg < 1e-6:
            continue
        pieces = max(1, int(math.ceil(seg / step_mm)))
        for j in range(1, pieces + 1):
            t = j / pieces
            out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
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
    grid_res = max(2, int(job.get("grid_res", 400)))

    bpy.ops.mesh.primitive_grid_add(x_subdivisions=grid_res, y_subdivisions=grid_res, size=1.0, location=(size_x / 2.0, size_y / 2.0, 0.0))
    terrain = bpy.context.active_object
    terrain.name = "Terrain"
    terrain.scale = (size_x / 2.0, size_y / 2.0, 1.0)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    image = bpy.data.images.load(job["heightmap_path"])
    tex = bpy.data.textures.new("HeightmapTex", type="IMAGE")
    tex.image = image

    displace = terrain.modifiers.new(name="Displace", type="DISPLACE")
    displace.texture = tex
    displace.texture_coords = "UV"
    displace.strength = float(job.get("z_scale", 1.0)) * float(job.get("z_range_mm", 0.0))
    displace.mid_level = 0.0

    bpy.context.view_layer.objects.active = terrain
    bpy.ops.object.modifier_apply(modifier=displace.name)
    _apply_rim_flatten(terrain, size_x, size_y, float(job.get("rim_mm", 3.0)))

    solidify = terrain.modifiers.new(name="Solidify", type="SOLIDIFY")
    solidify.thickness = base_mm
    solidify.offset = -1.0
    bpy.ops.object.modifier_apply(modifier=solidify.name)

    _enable_smooth_shading(terrain)
    return terrain


def _curve_from_points(points: list[tuple[float, float]], name: str) -> bpy.types.Object:
    cdata = bpy.data.curves.new(f"{name}Data", type="CURVE")
    cdata.dimensions = "3D"
    cdata.resolution_u = 24
    spline = cdata.splines.new(type="POLY")
    spline.points.add(len(points) - 1)
    for i, (x, y) in enumerate(points):
        spline.points[i].co = (x, y, 0.0, 1.0)
    cobj = bpy.data.objects.new(name, cdata)
    bpy.context.collection.objects.link(cobj)
    return cobj


def _create_track_inlay(job: dict, terrain_top: bpy.types.Object) -> tuple[bpy.types.Object | None, bpy.types.Object | None]:
    if not bool(job.get("track_inlay_enabled", True)):
        return None, None
    points = _resample_track(job.get("track_points_mm", []), 1.0)
    if len(points) < 2:
        return None, None

    groove_width = float(job.get("groove_width_mm", 2.6))
    groove_depth = float(job.get("groove_depth_mm", 1.6))
    groove_chamfer = float(job.get("groove_chamfer_mm", 0.4))
    clearance = float(job.get("track_clearance_mm", 0.2))
    relief = float(job.get("track_relief_mm", 0.6))
    top_radius = float(job.get("track_top_radius_mm", 0.8))

    track_width = max(0.4, groove_width - 2.0 * clearance)
    total_h = groove_depth + relief

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
    bpy.context.view_layer.objects.active = groove_curve
    bpy.ops.object.convert(target="MESH")
    groove_mesh = bpy.context.active_object

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
    bpy.context.view_layer.objects.active = track_curve
    bpy.ops.object.convert(target="MESH")
    track_mesh = bpy.context.active_object

    bev = track_mesh.modifiers.new(name="TopRound", type="BEVEL")
    bev.width = max(0.05, min(top_radius, track_width * 0.45))
    bev.segments = 3
    bev.limit_method = "ANGLE"
    bpy.ops.object.modifier_apply(modifier=bev.name)

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
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.export_mesh.stl(filepath=str(path), use_selection=True)


def main() -> None:
    if "--" not in sys.argv:
        raise RuntimeError("Percorso job.json mancante")

    job = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    out_base = Path(job.get("out_base_stl_path", "base_brown.stl"))
    out_water = Path(job.get("out_water_stl_path", "water.stl"))
    out_green = Path(job.get("out_green_stl_path", "green.stl"))
    out_detail = Path(job.get("out_detail_stl_path", "detail.stl"))
    out_track = Path(job.get("out_track_inlay_stl_path", "track_inlay_red.stl"))
    out_frame = Path(job.get("out_frame_stl_path", "frame.stl"))

    bpy.context.scene.unit_settings.system = "NONE"
    _clear_scene()

    base = _create_terrain(job)
    terrain_for_layers = base.copy()
    terrain_for_layers.data = base.data.copy()
    bpy.context.collection.objects.link(terrain_for_layers)

    groove, track_inlay = _create_track_inlay(job, terrain_for_layers)
    if groove is not None:
        _apply_boolean(base, groove, "DIFFERENCE")

    water, green, detail = _build_ams_layers(job, terrain_for_layers)

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

    _export_stl(base, out_base)
    _export_stl(water, out_water)
    _export_stl(green, out_green)
    _export_stl(detail, out_detail)
    _export_stl(track_inlay, out_track)

    if bool(job.get("separate_frame", True)):
        frame_obj = _create_test_frame_corner(job) if bool(job.get("test_mode", False)) else _create_frame(job)
        _export_stl(frame_obj, out_frame)


if __name__ == "__main__":
    main()
