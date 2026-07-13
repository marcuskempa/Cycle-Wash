"""Physical display-geometry policy for authoritative CycleWash STL parts."""

from __future__ import annotations

from dataclasses import dataclass
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
from cyclewash_structural_visualizer import AssemblyPart


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


__all__ = [
    "ASSEMBLY_SCALE_TO_M",
    "DRUM_DEPTH_M",
    "DRUM_EFFECTIVE_RADIUS_M",
    "GEAR_OUTSIDE_RADIUS_M",
    "GEAR_PITCH_RADIUS_M",
    "NormalizedGeometry",
    "normalize_stl_part",
]
