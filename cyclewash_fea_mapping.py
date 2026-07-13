"""Map physical Stage 1 FEA fields onto authoritative STL display surfaces."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from cyclewash_structural_visualizer import AssemblyPart


REGISTRATION_SCALE_WARNING_FRACTION = 0.10
PROJECTION_WARNING_TOLERANCE_M = 0.010
MAX_NEAREST_PAIR_COUNT = 1_000_000


@dataclass(frozen=True)
class MappedFeaFields:
    """Physical solver fields sampled onto one undeformed STL surface."""

    vertices_m: np.ndarray
    faces: np.ndarray
    phase_degrees: np.ndarray
    stress_pa: np.ndarray
    displacement_m: np.ndarray
    component_name: str
    source_path: Path | None
    geometry_metadata: dict[str, Any]
    metadata: dict[str, Any]


def _error_statistics(errors: np.ndarray) -> dict[str, float]:
    return {
        "median": float(np.median(errors)),
        "p95": float(np.percentile(errors, 95)),
        "max": float(np.max(errors)),
    }


def _robust_bounds(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if len(points) < 128:
        return points.min(axis=0), points.max(axis=0)
    lower = np.quantile(points, 0.001, axis=0)
    upper = np.quantile(points, 0.999, axis=0)
    if np.any(upper <= lower):
        return points.min(axis=0), points.max(axis=0)
    return lower, upper


def tetrahedral_surface_nodes(tetrahedra: np.ndarray) -> np.ndarray:
    """Return unique node indices that lie on a tetrahedral mesh boundary."""

    tetrahedra = np.asarray(tetrahedra, dtype=np.int64)
    if tetrahedra.ndim != 2 or tetrahedra.shape[1:] != (4,) or len(tetrahedra) == 0:
        raise ValueError("FEA tetrahedra must have shape (element_count, 4) and be non-empty")
    faces = np.concatenate(
        (
            tetrahedra[:, (1, 2, 3)],
            tetrahedra[:, (0, 3, 2)],
            tetrahedra[:, (0, 1, 3)],
            tetrahedra[:, (0, 2, 1)],
        ),
        axis=0,
    )
    unique_faces, counts = np.unique(np.sort(faces, axis=1), axis=0, return_counts=True)
    boundary_faces = unique_faces[counts == 1]
    if boundary_faces.size == 0:
        raise ValueError("FEA tetrahedra do not contain a boundary surface")
    return np.unique(boundary_faces)


def component_projection_nodes(
    component_name: str,
    solver_vertices_m: np.ndarray,
    surface_nodes: np.ndarray,
) -> tuple[np.ndarray, str]:
    """Select the solver surface representing the displayed component envelope."""

    if component_name != "gear":
        return surface_nodes, "full exterior surface"

    vertices = np.asarray(solver_vertices_m, dtype=float)
    center = (vertices.min(axis=0) + vertices.max(axis=0)) / 2.0
    radial = np.linalg.norm(vertices[:, 1:3] - center[1:3], axis=1)
    maximum_radius = float(np.max(radial[surface_nodes]))
    candidates = surface_nodes[radial[surface_nodes] >= 0.5 * maximum_radius]
    if len(candidates) >= 4 and np.all(np.ptp(vertices[candidates], axis=0) > 0.0):
        return candidates, "outer sprocket surface"
    return surface_nodes, "full exterior surface (outer sprocket domain unavailable)"


def tetrahedral_edge_scale(
    vertices: np.ndarray, tetrahedra: np.ndarray
) -> dict[str, float]:
    """Return representative tetrahedral edge lengths for projection diagnostics."""

    tetrahedra = np.asarray(tetrahedra, dtype=np.int64)
    edge_pairs = np.concatenate(
        tuple(
            tetrahedra[:, pair]
            for pair in ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))
        ),
        axis=0,
    )
    vertices = np.asarray(vertices, dtype=float)
    lengths = np.linalg.norm(vertices[edge_pairs[:, 0]] - vertices[edge_pairs[:, 1]], axis=1)
    if not len(lengths) or np.any(lengths <= 0.0):
        raise ValueError("FEA tetrahedra must have positive edge lengths")
    return {
        "median_edge": float(np.median(lengths)),
        "p95_edge": float(np.percentile(lengths, 95)),
        "maximum_edge": float(np.max(lengths)),
    }


def chunked_nearest_indices(
    query_points: np.ndarray,
    reference_points: np.ndarray,
    max_pair_count: int = MAX_NEAREST_PAIR_COUNT,
) -> tuple[np.ndarray, np.ndarray]:
    """Find nearest reference nodes without allocating a full distance matrix."""

    query_points = np.asarray(query_points, dtype=float)
    reference_points = np.asarray(reference_points, dtype=float)
    if len(query_points) == 0 or len(reference_points) == 0:
        raise ValueError("nearest projection requires non-empty point arrays")
    if not np.all(np.isfinite(query_points)) or not np.all(np.isfinite(reference_points)):
        raise ValueError("nearest projection requires finite point arrays")

    max_pairs = max(1, int(max_pair_count))
    query_chunk_size = max(1, min(len(query_points), int(math.sqrt(max_pairs))))
    nearest = np.empty(len(query_points), dtype=np.int64)
    best_distances = np.empty(len(query_points), dtype=float)
    reference_norm = np.einsum("ij,ij->i", reference_points, reference_points)
    for query_start in range(0, len(query_points), query_chunk_size):
        query_stop = min(query_start + query_chunk_size, len(query_points))
        query_chunk = query_points[query_start:query_stop]
        reference_chunk_size = max(1, max_pairs // len(query_chunk))
        local_best = np.full(len(query_chunk), np.inf, dtype=float)
        local_indices = np.zeros(len(query_chunk), dtype=np.int64)
        query_norm = np.einsum("ij,ij->i", query_chunk, query_chunk)[:, None]
        for reference_start in range(0, len(reference_points), reference_chunk_size):
            reference_stop = min(reference_start + reference_chunk_size, len(reference_points))
            candidate = reference_points[reference_start:reference_stop]
            squared = query_norm + reference_norm[None, reference_start:reference_stop]
            squared -= 2.0 * (query_chunk @ candidate.T)
            np.maximum(squared, 0.0, out=squared)
            candidate_indices = np.argmin(squared, axis=1)
            candidate_distances = squared[np.arange(len(query_chunk)), candidate_indices]
            improved = candidate_distances < local_best
            local_best[improved] = candidate_distances[improved]
            local_indices[improved] = reference_start + candidate_indices[improved]
        nearest[query_start:query_stop] = local_indices
        best_distances[query_start:query_stop] = np.sqrt(local_best)
    return nearest, best_distances


def register_display_to_solver(
    display_vertices_m: np.ndarray,
    solver_vertices_m: np.ndarray,
    component_name: str,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Affine-register an STL copy to solver bounds for field lookup only."""

    display_vertices_m = np.asarray(display_vertices_m, dtype=float)
    solver_vertices_m = np.asarray(solver_vertices_m, dtype=float)
    source_raw_min = display_vertices_m.min(axis=0)
    source_raw_max = display_vertices_m.max(axis=0)
    source_min, source_max = _robust_bounds(display_vertices_m)
    target_min = solver_vertices_m.min(axis=0)
    target_max = solver_vertices_m.max(axis=0)
    source_span = source_max - source_min
    target_span = target_max - target_min
    if np.any(source_span <= 0.0) or np.any(target_span <= 0.0):
        raise ValueError(f"{component_name} registration requires positive bounds on every axis")

    source_center = (source_min + source_max) / 2.0
    target_center = (target_min + target_max) / 2.0
    scale_factors = target_span / source_span
    translation = target_center - source_center * scale_factors
    registered = display_vertices_m * scale_factors + translation

    mismatch = np.abs(scale_factors - 1.0)
    anisotropy_ratio = float(np.max(scale_factors) / np.min(scale_factors))
    radial_anisotropy_ratio = float(
        max(scale_factors[1], scale_factors[2]) / min(scale_factors[1], scale_factors[2])
    )
    material_anisotropy = (
        anisotropy_ratio - 1.0 > REGISTRATION_SCALE_WARNING_FRACTION
        or radial_anisotropy_ratio - 1.0 > REGISTRATION_SCALE_WARNING_FRACTION
    )
    warning = bool(
        np.any(mismatch > REGISTRATION_SCALE_WARNING_FRACTION) or material_anisotropy
    )
    warning_message = (
        "Visualization registration materially rescales/distorts CAD coordinates; "
        "this is not physical geometric agreement."
        if warning
        else "Visualization registration is not evidence of physical geometric agreement."
    )
    metadata = {
        "method": (
            "Component-axis affine registration for visualization field transfer; "
            "not physical geometric agreement."
        ),
        "component": component_name,
        "longitudinal_axis": "Blender X",
        "translation_m": translation.tolist(),
        "longitudinal_scale_factor": float(scale_factors[0]),
        "radial_scale_factors": {
            "y": float(scale_factors[1]),
            "z": float(scale_factors[2]),
        },
        "source_bounds_m": {
            "raw_min": source_raw_min.tolist(),
            "raw_max": source_raw_max.tolist(),
            "registration_min": source_min.tolist(),
            "registration_max": source_max.tolist(),
        },
        "target_solver_bounds_m": {
            "min": target_min.tolist(),
            "max": target_max.tolist(),
        },
        "scale_mismatch": {
            "longitudinal_fraction": float(mismatch[0]),
            "radial_y_fraction": float(mismatch[1]),
            "radial_z_fraction": float(mismatch[2]),
            "maximum_fraction": float(np.max(mismatch)),
            "anisotropy_ratio": anisotropy_ratio,
            "radial_anisotropy_ratio": radial_anisotropy_ratio,
        },
        "material_anisotropy": bool(material_anisotropy),
        "warning_threshold_fraction": REGISTRATION_SCALE_WARNING_FRACTION,
        "warning": warning,
        "warning_message": warning_message,
    }
    return registered, metadata


def map_fea_fields_to_stl(
    part: AssemblyPart,
    component: Any,
    component_name: str,
    *,
    source_path: Path | None = None,
    geometry_metadata: Mapping[str, Any] | None = None,
) -> MappedFeaFields:
    """Sample physical component fields onto every authoritative STL vertex."""

    display_vertices = np.asarray(part.vertices, dtype=float)
    faces = np.asarray(part.faces, dtype=np.int64)
    solver_vertices = np.asarray(component.vertices_m, dtype=float)
    tetrahedra = np.asarray(component.tetrahedra, dtype=np.int64)
    stress = np.asarray(component.von_mises_pa, dtype=float)
    displacement = np.asarray(component.displacement_m, dtype=float)
    phases = np.asarray(component.phase_degrees, dtype=float)

    if display_vertices.ndim != 2 or display_vertices.shape[1:] != (3,) or not len(display_vertices):
        raise ValueError("display STL vertices must have shape (node_count, 3) and be non-empty")
    if faces.ndim != 2 or faces.shape[1:] != (3,) or not len(faces):
        raise ValueError("display STL faces must have shape (triangle_count, 3) and be non-empty")
    if stress.ndim != 2 or stress.shape[1] != len(solver_vertices) or not len(stress):
        raise ValueError("FEA stress must have shape (phase_count, solver_node_count)")
    if displacement.shape != (len(stress), len(solver_vertices), 3):
        raise ValueError("FEA displacement must have shape (phase_count, solver_node_count, 3)")
    if phases.shape != (len(stress),):
        raise ValueError("FEA phase degrees must contain one value per field phase")
    if not np.all(np.isfinite(stress)):
        raise ValueError("FEA mapping requires finite physical stress values")
    if not np.all(np.isfinite(displacement)):
        raise ValueError("FEA mapping requires finite physical displacement values")

    surface_nodes = tetrahedral_surface_nodes(tetrahedra)
    projection_nodes, projection_domain = component_projection_nodes(
        component_name, solver_vertices, surface_nodes
    )
    solver_surface = solver_vertices[projection_nodes]
    registered_display, registration = register_display_to_solver(
        display_vertices, solver_surface, component_name
    )
    _, raw_errors = chunked_nearest_indices(display_vertices, solver_surface)
    display_center = (display_vertices.min(axis=0) + display_vertices.max(axis=0)) / 2.0
    solver_center = (solver_surface.min(axis=0) + solver_surface.max(axis=0)) / 2.0
    center_aligned = display_vertices + (solver_center - display_center)
    _, center_errors = chunked_nearest_indices(center_aligned, solver_surface)
    nearest_surface, errors = chunked_nearest_indices(registered_display, solver_surface)
    nearest_solver_nodes = projection_nodes[nearest_surface]

    projection = _error_statistics(errors)
    mesh_scale = tetrahedral_edge_scale(solver_vertices, tetrahedra)
    projection_warning = projection["p95"] > PROJECTION_WARNING_TOLERANCE_M
    metadata = {
        "component": component_name,
        "registration": registration,
        "pre_registration_mismatch": {
            "raw_unaligned_error_m": _error_statistics(raw_errors),
            "center_aligned_error_m": _error_statistics(center_errors),
        },
        "projection_error_m": projection,
        "solver_mesh_scale_m": mesh_scale,
        "p95_error_over_p95_edge": projection["p95"] / mesh_scale["p95_edge"],
        "projection_warning_tolerance_m": PROJECTION_WARNING_TOLERANCE_M,
        "projection_warning": projection_warning,
        "warning": bool(projection_warning or registration["warning"]),
        "solver_surface_node_count": int(len(surface_nodes)),
        "solver_projection_node_count": int(len(projection_nodes)),
        "projection_domain": projection_domain,
        "method": "chunked nearest-surface-node projection after explicit component registration",
    }
    return MappedFeaFields(
        vertices_m=display_vertices.copy(),
        faces=faces.copy(),
        phase_degrees=phases.copy(),
        stress_pa=stress[:, nearest_solver_nodes].copy(),
        displacement_m=displacement[:, nearest_solver_nodes, :].copy(),
        component_name=component_name,
        source_path=None if source_path is None else Path(source_path),
        geometry_metadata=dict(geometry_metadata or {}),
        metadata=metadata,
    )


__all__ = [
    "MappedFeaFields",
    "chunked_nearest_indices",
    "component_projection_nodes",
    "map_fea_fields_to_stl",
    "register_display_to_solver",
    "tetrahedral_edge_scale",
    "tetrahedral_surface_nodes",
]
