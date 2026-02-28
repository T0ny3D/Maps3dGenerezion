"""Export STL parts to a single Bambu Studio-compatible 3MF file.

This module packs multiple STL parts (base, water, green, detail, track, frame)
into one .3mf with separate named objects, suitable for Bambu Studio.
Colors are set as "hints" via ColorGroup when supported.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import lib3mf
import trimesh

logger = logging.getLogger(__name__)


class Export3MFError(Exception):
    """Raised when 3MF export fails."""


# Color palette from SPEC.md (RGB 0-255)
_OBJECT_COLORS = {
    "base": (120, 80, 50),        # brown
    "water": (50, 120, 255),      # blue
    "green": (40, 180, 80),       # green
    "detail": (230, 220, 200),    # light tan
    "track": (230, 40, 40),       # red
    "frame": (160, 160, 165),     # gray
}


def _rgb_to_srgb_int(r: int, g: int, b: int, a: int = 255) -> int:
    """Convert RGBA (0-255) to lib3mf sRGB uint32 (A B G R)."""
    # lib3mf expects sRGB packed as 0xAABBGGRR
    return ((a & 0xFF) << 24) | ((b & 0xFF) << 16) | ((g & 0xFF) << 8) | (r & 0xFF)


def export_stls_to_3mf(
    stl_paths: dict[str, Path],
    output_3mf_path: Path,
) -> Path:
    """
    Pack multiple STL files into a single 3MF with separate named objects.

    Args:
        stl_paths: Dict mapping object names (base, water, green, detail, track, frame)
                   to their STL file paths. Missing keys are skipped.
        output_3mf_path: Output 3MF file path.

    Returns:
        Path to the generated 3MF file.

    Raises:
        Export3MFError: If STL loading fails or 3MF creation fails.
    """
    output_3mf_path = Path(output_3mf_path)
    output_3mf_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        wrapper = lib3mf.Wrapper()
        model = wrapper.CreateModel()
        identity_transform = wrapper.GetIdentityTransform()

        added_objects: list[str] = []

        # Iterate in a stable order (nice for slicers)
        for obj_name in ["base", "water", "green", "detail", "track", "frame"]:
            if obj_name not in stl_paths:
                continue

            stl_path = Path(stl_paths[obj_name])
            if not stl_path.exists():
                logger.warning("STL not found, skipping %s: %s", obj_name, stl_path)
                continue

            try:
                mesh = trimesh.load_mesh(str(stl_path))

                # If trimesh returns a Scene, merge geometries
                if isinstance(mesh, trimesh.Scene):
                    if not mesh.geometry:
                        logger.warning("Empty scene for %s: %s", obj_name, stl_path)
                        continue
                    mesh = trimesh.util.concatenate(tuple(mesh.geometry.values()))

                # Ensure we have a Trimesh
                if not isinstance(mesh, trimesh.Trimesh):
                    mesh = trimesh.Trimesh(vertices=mesh.vertices, faces=mesh.faces, process=False)

                # Basic empty checks
                if getattr(mesh, "faces", None) is None or mesh.faces.size == 0 or mesh.vertices.size == 0:
                    logger.warning("Empty mesh for %s: %s", obj_name, stl_path)
                    continue

                # Make sure faces are triangles
                if mesh.faces.shape[1] != 3:
                    mesh = mesh.triangulate()

                # Create mesh object in lib3mf
                mesh_obj = model.AddMeshObject()
                mesh_obj.SetName(obj_name)

                # Add vertices
                vertices = mesh.vertices.astype("float32")
                for vx, vy, vz in vertices:
                    mesh_obj.AddVertex(float(vx), float(vy), float(vz))

                # Add triangles
                faces = mesh.faces.astype("uint32")
                for f0, f1, f2 in faces:
                    tri = lib3mf.TRIANGLE()
                    tri.m_nIndices[0] = int(f0)
                    tri.m_nIndices[1] = int(f1)
                    tri.m_nIndices[2] = int(f2)
                    mesh_obj.AddTriangle(tri)

                # Add to build with identity transform
                model.AddBuildItem(mesh_obj, identity_transform)

                # Assign suggested color (hint)
                if obj_name in _OBJECT_COLORS:
                    r, g, b = _OBJECT_COLORS[obj_name]
                    color_int = _rgb_to_srgb_int(r, g, b, 255)
                    color_group = model.AddColorGroup()
                    color_group.AddColor(color_int)
                    # Object-level property index 0 in that color group
                    mesh_obj.SetObjectLevelProperty(color_group, 0)

                added_objects.append(obj_name)
                logger.info("Added object '%s' from %s", obj_name, stl_path.name)

            except Exception as exc:
                raise Export3MFError(f"Failed to load STL '{stl_path}': {exc}") from exc

        if not added_objects:
            raise Export3MFError("No valid STL files were loaded; 3MF would be empty.")

        # Save 3MF
        try:
            writer = model.QueryWriter("3mf")
            writer.WriteToFile(str(output_3mf_path))
        except Exception as exc:
            raise Export3MFError(f"Failed to write 3MF file: {exc}") from exc

        logger.info("Exported 3MF with objects: %s", ", ".join(added_objects))
        logger.info("Output file: %s", output_3mf_path)
        return output_3mf_path

    except Export3MFError:
        raise
    except Exception as exc:
        raise Export3MFError(f"Unexpected error during 3MF export: {exc}") from exc


def create_3mf_from_stl_output_base(
    output_base: Path,
    test_mode: bool = False,
    include_objects: Optional[list[str]] = None,
) -> Path:
    """
    Convenience function: given an output base path like '/path/to/mymap.stl',
    find all related STL files and pack them into a 3MF.

    Expected STL naming:
    - Normal: {stem}_base_brown.stl, {stem}_water.stl, {stem}_track_inlay_red.stl, etc.
    - Test:   {stem}_test_base_brown.stl, {stem}_test_water.stl, etc.

    Output 3MF:
    - Normal: {stem}.3mf
    - Test:   {stem}_test.3mf
    """
    output_base = Path(output_base)
    stem = output_base.stem
    parent = output_base.parent

    suffix = "_test" if test_mode else ""

    stl_patterns = {
        "base": f"{stem}{suffix}_base_brown.stl",
        "water": f"{stem}{suffix}_water.stl",
        "green": f"{stem}{suffix}_green.stl",
        "detail": f"{stem}{suffix}_detail.stl",
        "track": f"{stem}{suffix}_track_inlay_red.stl",
        "frame": f"{stem}{suffix}_frame.stl",
    }

    if include_objects:
        stl_patterns = {k: v for k, v in stl_patterns.items() if k in include_objects}

    stl_paths: dict[str, Path] = {}
    for obj_name, filename in stl_patterns.items():
        p = parent / filename
        if p.exists():
            stl_paths[obj_name] = p

    if not stl_paths:
        raise Export3MFError(f"No STL files found in {parent} matching pattern {stem}{suffix}_*.stl")

    output_3mf_path = parent / f"{stem}{suffix}.3mf"
    return export_stls_to_3mf(stl_paths, output_3mf_path)
