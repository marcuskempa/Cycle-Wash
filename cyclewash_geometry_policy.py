"""Physical display-geometry policy for authoritative CycleWash STL parts."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np

from cyclewash_dimensions import (
    ASSEMBLY_SCALE_TO_M,
    DRUM_DEPTH_M,
    DRUM_EFFECTIVE_RADIUS_M,
    GEAR_OUTSIDE_RADIUS_M,
    GEAR_PITCH_RADIUS_M,
    GEAR_THICKNESS_M,
    GEAR_TOOTH_COUNT,
    SHAFT_DIAMETER_M,
    SHAFT_LENGTH_M,
)
from cyclewash_structural_visualizer import AssemblyPart, Transform


DOOR_CLOSED_ROTATION_DEGREES = 90.0
DOOR_CLOSED_AXIS = (0.0, 0.0, -1.0)


@dataclass(frozen=True)
class NormalizedGeometry:
    """An STL part converted to documented physical display coordinates."""

    part: AssemblyPart
    source_scale_description: str
    metadata: dict[str, Any]


def _part_with_vertices(part: AssemblyPart, vertices_m: np.ndarray) -> AssemblyPart:
    return AssemblyPart(
        name=part.name,
        local_vertices=np.asarray(vertices_m, dtype=float),
        faces=np.asarray(part.faces, dtype=np.int64),
        material_color=part.material_color,
        component_kind=part.component_kind,
    )


def _center_preserving_normalization(
    part: AssemblyPart,
    target_span_m: tuple[float, float, float],
) -> tuple[AssemblyPart, np.ndarray, np.ndarray]:
    source_vertices = np.asarray(part.vertices, dtype=float)
    source_min = np.min(source_vertices, axis=0)
    source_max = np.max(source_vertices, axis=0)
    source_span = source_max - source_min
    if np.any(source_span <= 0.0) or not np.all(np.isfinite(source_span)):
        raise ValueError(f"{part.name} STL must have a finite, non-zero 3D envelope")

    source_center = (source_min + source_max) / 2.0
    physical_center = source_center * ASSEMBLY_SCALE_TO_M
    scale_factors = np.asarray(target_span_m, dtype=float) / source_span
    vertices_m = physical_center + (source_vertices - source_center) * scale_factors
    return _part_with_vertices(part, vertices_m), source_center, scale_factors


def normalize_stl_part(part: AssemblyPart) -> NormalizedGeometry:
    """Apply the approved uniform assembly or drivetrain STL normalization."""

    normalized_name = part.name.lower().replace("_", " ").strip()
    source_vertices = np.asarray(part.vertices, dtype=float)

    if "shaft" in normalized_name:
        normalized_part, source_center, scale_factors = _center_preserving_normalization(
            part,
            (SHAFT_LENGTH_M, SHAFT_DIAMETER_M, SHAFT_DIAMETER_M),
        )
        metadata = {
            "normalization": "shaft_engineering_dimensions",
            "assembly_scale_to_m": ASSEMBLY_SCALE_TO_M,
            "target_length_m": SHAFT_LENGTH_M,
            "target_diameter_m": SHAFT_DIAMETER_M,
            "source_center": source_center.tolist(),
            "axis_scale_factors": scale_factors.tolist(),
        }
        description = "25 mm diameter x 110 mm shaft normalization on Blender X"
    elif "gear" in normalized_name or "cog" in normalized_name:
        outside_diameter_m = 2.0 * GEAR_OUTSIDE_RADIUS_M
        normalized_part, source_center, scale_factors = _center_preserving_normalization(
            part,
            (GEAR_THICKNESS_M, outside_diameter_m, outside_diameter_m),
        )
        metadata = {
            "normalization": "gear_engineering_dimensions",
            "assembly_scale_to_m": ASSEMBLY_SCALE_TO_M,
            "tooth_count": GEAR_TOOTH_COUNT,
            "pitch_radius_m": GEAR_PITCH_RADIUS_M,
            "outside_radius_m": GEAR_OUTSIDE_RADIUS_M,
            "target_thickness_m": GEAR_THICKNESS_M,
            "source_center": source_center.tolist(),
            "axis_scale_factors": scale_factors.tolist(),
        }
        description = "32T, 12.7 mm-pitch sprocket normalization on Blender X"
    else:
        normalized_part = _part_with_vertices(
            part, source_vertices * ASSEMBLY_SCALE_TO_M
        )
        metadata = {
            "normalization": "uniform_assembly_scale",
            "assembly_scale_to_m": ASSEMBLY_SCALE_TO_M,
            "axis_scale_factors": [ASSEMBLY_SCALE_TO_M] * 3,
        }
        description = "uniform Blender assembly scale: 0.340 m per source unit"

    return NormalizedGeometry(
        part=normalized_part,
        source_scale_description=description,
        metadata=metadata,
    )


def estimate_door_hinge_origin(
    part: AssemblyPart, hinge_side: str
) -> tuple[float, float, float]:
    """Estimate a hinge origin from vertices on the selected door boundary."""

    vertices = np.asarray(part.vertices, dtype=float)
    bounds_min = vertices.min(axis=0)
    bounds_max = vertices.max(axis=0)
    center = (bounds_min + bounds_max) / 2.0
    side_map = {
        "min x": (0, bounds_min[0]),
        "max x": (0, bounds_max[0]),
        "min y": (1, bounds_min[1]),
        "max y": (1, bounds_max[1]),
    }
    selection = side_map.get(hinge_side.lower())
    if selection is None:
        origin = center
    else:
        axis_index, extreme = selection
        axis_span = float(bounds_max[axis_index] - bounds_min[axis_index])
        tolerance = max(axis_span * 1.0e-6, 1.0e-9)
        boundary_mask = np.isclose(
            vertices[:, axis_index], extreme, atol=tolerance, rtol=0.0
        )
        origin = vertices[boundary_mask].mean(axis=0)
        origin[axis_index] = extreme
    return (float(origin[0]), float(origin[1]), float(origin[2]))


def closed_door_pose_angle(opening_degrees: float) -> float:
    """Map a door opening angle onto the exported STL's open-pose rotation."""

    opening = float(opening_degrees)
    if not math.isfinite(opening) or not 0.0 <= opening <= 90.0:
        raise ValueError("door opening angle must be between 0 and 90 degrees")
    return DOOR_CLOSED_ROTATION_DEGREES - opening


def apply_closed_door_pose(part: AssemblyPart) -> AssemblyPart:
    """Return door geometry in the approved closed pose; leave other parts unchanged."""

    normalized_name = part.name.lower().replace("_", " ").strip()
    if "door" not in normalized_name:
        return part
    world_part = _part_with_vertices(part, part.vertices)
    hinge_origin = estimate_door_hinge_origin(world_part, "min X")
    return world_part.transformed(
        Transform.from_rotation(
            DOOR_CLOSED_AXIS,
            closed_door_pose_angle(0.0),
            origin=hinge_origin,
        )
    )


__all__ = [
    "ASSEMBLY_SCALE_TO_M",
    "DRUM_DEPTH_M",
    "DRUM_EFFECTIVE_RADIUS_M",
    "GEAR_OUTSIDE_RADIUS_M",
    "GEAR_PITCH_RADIUS_M",
    "NormalizedGeometry",
    "apply_closed_door_pose",
    "closed_door_pose_angle",
    "estimate_door_hinge_origin",
    "normalize_stl_part",
]
