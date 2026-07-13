"""Optional Gmsh/SfePy solver for the CycleWash Stage 1 analysis.

Geometry, loads, displacement, and stress use SI units. Heavy solver imports are
deliberately lazy so the Streamlit environment can import this module without the
isolated FEA dependencies installed.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import asdict, dataclass, fields
from io import StringIO
import json
import math
from pathlib import Path
import sys
from typing import Any, Callable, Mapping

import numpy as np

from cyclewash_engineering_model import (
    EngineeringInputs,
    MaterialProperties,
    SHAFT_LOAD_BAND_WIDTH_M,
    calculate_engineering_loads,
    canonical_request_identity,
    normalize_mesh_levels,
)
from cyclewash_fea_results import (
    SCHEMA_VERSION,
    ComponentFieldResult,
    MeshConvergenceResult,
    MeshMetrics,
    Stage1FeaPackage,
    evaluate_mesh_convergence,
    save_stage1_package,
)


PHASE_DEGREES = tuple(float(value) for value in range(0, 360, 30))
SYMMETRIC_TENSOR_COMPONENTS = ("xx", "yy", "zz", "xy", "xz", "yz")
FACTOR_OF_SAFETY_CAP = 1.0e12
MESH_SIZES_M = {
    "shaft": {"coarse": 0.0080, "medium": 0.0050, "fine": 0.0035},
    "gear": {"coarse": 0.0120, "medium": 0.0080, "fine": 0.0055},
    "drum": {"coarse": 0.0550, "medium": 0.0400, "fine": 0.0300},
}


@dataclass(frozen=True)
class SolverMesh:
    """Tetrahedral solver mesh with explicit boundary selections."""

    vertices_m: np.ndarray
    tetrahedra: np.ndarray
    boundary_nodes: Mapping[str, np.ndarray]
    boundary_facets: Mapping[str, np.ndarray]
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        vertices = np.asarray(self.vertices_m, dtype=np.float64)
        tetrahedra = np.asarray(self.tetrahedra, dtype=np.int64)
        if vertices.ndim != 2 or vertices.shape[1] != 3 or not vertices.size:
            raise ValueError("vertices_m must have shape (node_count, 3)")
        if tetrahedra.ndim != 2 or tetrahedra.shape[1] != 4 or not tetrahedra.size:
            raise ValueError("tetrahedra must have shape (element_count, 4)")
        if not np.isfinite(vertices).all():
            raise ValueError("vertices_m contains non-finite coordinates")
        if tetrahedra.min() < 0 or tetrahedra.max() >= vertices.shape[0]:
            raise ValueError("tetrahedra contains an invalid node index")
        volumes = _signed_tetra_volumes(vertices, tetrahedra)
        if not np.all(volumes > 0.0):
            raise ValueError("tetrahedra must have positive signed volume")
        normalized_nodes = {
            name: np.asarray(indices, dtype=np.int64)
            for name, indices in self.boundary_nodes.items()
        }
        normalized_facets = {
            name: np.asarray(facets, dtype=np.int64).reshape((-1, 3))
            for name, facets in self.boundary_facets.items()
        }
        for name, indices in normalized_nodes.items():
            if not indices.size:
                raise ValueError(f"boundary node selection {name!r} is empty")
        for name, facets in normalized_facets.items():
            if not facets.size:
                raise ValueError(f"boundary facet selection {name!r} is empty")
        object.__setattr__(self, "vertices_m", vertices)
        object.__setattr__(self, "tetrahedra", tetrahedra)
        object.__setattr__(self, "boundary_nodes", normalized_nodes)
        object.__setattr__(self, "boundary_facets", normalized_facets)
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class _ElasticityFields:
    displacement_m: np.ndarray
    von_mises_pa: np.ndarray
    element_strain: np.ndarray
    nodal_maximum_shear_pa: np.ndarray
    nodal_factor_of_safety: np.ndarray


def _require_fea_dependencies() -> tuple[Any, Any]:
    try:
        import gmsh
        import sfepy
    except ImportError as error:
        raise RuntimeError(
            "CycleWash FEA requires gmsh and sfepy in work/.fea-venv"
        ) from error
    return gmsh, sfepy


def _signed_tetra_volumes(vertices: np.ndarray, tetrahedra: np.ndarray) -> np.ndarray:
    points = vertices[tetrahedra]
    return np.einsum(
        "ij,ij->i",
        points[:, 1] - points[:, 0],
        np.cross(points[:, 2] - points[:, 0], points[:, 3] - points[:, 0]),
    ) / 6.0


def _canonicalize_tetrahedra(
    vertices: np.ndarray, tetrahedra: np.ndarray
) -> np.ndarray:
    tetrahedra = np.asarray(tetrahedra, dtype=np.int64).copy()
    volumes = _signed_tetra_volumes(vertices, tetrahedra)
    scale = max(float(np.ptp(vertices, axis=0).max()), 1.0e-12)
    tolerance = np.finfo(np.float64).eps * scale**3 * 64.0
    if np.any(np.abs(volumes) <= tolerance):
        raise ValueError("Gmsh produced a degenerate tetrahedron")
    negative = volumes < 0.0
    tetrahedra[negative, 2], tetrahedra[negative, 3] = (
        tetrahedra[negative, 3].copy(),
        tetrahedra[negative, 2].copy(),
    )
    return tetrahedra


def _von_mises_from_voigt(stress: np.ndarray) -> np.ndarray:
    stress = np.asarray(stress, dtype=np.float64)
    if stress.ndim != 2 or stress.shape[1] != 6:
        raise ValueError("stress must have shape (count, 6)")
    xx, yy, zz, xy, xz, yz = stress.T
    return np.sqrt(
        0.5 * ((xx - yy) ** 2 + (yy - zz) ** 2 + (xx - zz) ** 2)
        + 3.0 * (xy**2 + xz**2 + yz**2)
    )


def volume_weighted_nodal_stress(
    vertices: np.ndarray, tetrahedra: np.ndarray, element_stress_voigt: np.ndarray
) -> np.ndarray:
    """Volume-average element stress tensors in xx,yy,zz,xy,xz,yz order."""

    vertices = np.asarray(vertices, dtype=np.float64)
    tetrahedra = np.asarray(tetrahedra, dtype=np.int64)
    element_stress = np.asarray(element_stress_voigt, dtype=np.float64)
    if element_stress.shape != (tetrahedra.shape[0], 6):
        raise ValueError("element_stress_voigt must have shape (element_count, 6)")
    volumes = np.abs(_signed_tetra_volumes(vertices, tetrahedra))
    nodal_stress = np.zeros((vertices.shape[0], 6), dtype=np.float64)
    nodal_volume = np.zeros(vertices.shape[0], dtype=np.float64)
    weighted = element_stress * volumes[:, None]
    for local_node in range(4):
        indices = tetrahedra[:, local_node]
        np.add.at(nodal_stress, indices, weighted)
        np.add.at(nodal_volume, indices, volumes)
    if np.any(nodal_volume <= 0.0):
        raise ValueError("mesh contains nodes unused by positive-volume tetrahedra")
    nodal_stress /= nodal_volume[:, None]
    return nodal_stress


def volume_weighted_nodal_von_mises(
    vertices: np.ndarray, tetrahedra: np.ndarray, element_stress_voigt: np.ndarray
) -> np.ndarray:
    """Volume-average element stress tensors at nodes, then evaluate von Mises."""

    return _von_mises_from_voigt(
        volume_weighted_nodal_stress(vertices, tetrahedra, element_stress_voigt)
    )


def maximum_shear_from_stress_voigt(stress_voigt: np.ndarray) -> np.ndarray:
    """Return Tresca maximum shear from principal stresses, in input stress units.

    Symmetric tensors use ``xx, yy, zz, xy, xz, yz`` ordering, with tensor
    shear components in the off-diagonal positions.
    """

    stress = np.asarray(stress_voigt, dtype=np.float64)
    if stress.ndim != 2 or stress.shape[1] != 6:
        raise ValueError("stress_voigt must have shape (count, 6)")
    tensors = np.zeros((stress.shape[0], 3, 3), dtype=np.float64)
    tensors[:, 0, 0], tensors[:, 1, 1], tensors[:, 2, 2] = stress[:, :3].T
    tensors[:, 0, 1] = tensors[:, 1, 0] = stress[:, 3]
    tensors[:, 0, 2] = tensors[:, 2, 0] = stress[:, 4]
    tensors[:, 1, 2] = tensors[:, 2, 1] = stress[:, 5]
    principal = np.linalg.eigvalsh(tensors)
    return 0.5 * (principal[:, -1] - principal[:, 0])


def nodal_factor_of_safety(
    von_mises_pa: np.ndarray, yield_strength_pa: float
) -> np.ndarray:
    """Return yield/von-Mises FoS capped at a finite value for zero stress."""

    stress = np.asarray(von_mises_pa, dtype=np.float64)
    if not math.isfinite(yield_strength_pa) or yield_strength_pa <= 0.0:
        raise ValueError("yield_strength_pa must be finite and positive")
    denominator_floor = yield_strength_pa / FACTOR_OF_SAFETY_CAP
    factor = yield_strength_pa / np.maximum(stress, denominator_floor)
    return np.minimum(factor, FACTOR_OF_SAFETY_CAP)


def nodes_outside_constraint_zone(
    vertices: np.ndarray,
    constrained_nodes: np.ndarray,
    nominal_element_size_m: float,
) -> np.ndarray:
    """Mask nodes farther than one nominal size from actual constrained nodes."""

    vertices = np.asarray(vertices, dtype=np.float64)
    constrained = np.asarray(constrained_nodes, dtype=np.int64)
    if constrained.size == 0:
        raise ValueError("constrained_nodes must not be empty")
    if not math.isfinite(nominal_element_size_m) or nominal_element_size_m <= 0.0:
        raise ValueError("nominal_element_size_m must be finite and positive")
    fixed_points = vertices[constrained]
    minimum_squared = np.full(vertices.shape[0], np.inf)
    for start in range(0, fixed_points.shape[0], 256):
        block = fixed_points[start : start + 256]
        squared = np.sum((vertices[:, None, :] - block[None, :, :]) ** 2, axis=2)
        minimum_squared = np.minimum(minimum_squared, squared.min(axis=1))
    return minimum_squared > nominal_element_size_m**2


def unconstrained_phase_stress_percentiles(
    vertices: np.ndarray,
    von_mises_pa: np.ndarray,
    constrained_nodes: np.ndarray,
    nominal_element_size_m: float,
    percentile: float = 95.0,
) -> np.ndarray:
    """Calculate one unconstrained stress percentile per load phase."""

    stress = np.asarray(von_mises_pa, dtype=np.float64)
    mask = nodes_outside_constraint_zone(
        vertices, constrained_nodes, nominal_element_size_m
    )
    if not np.any(mask):
        raise ValueError("constraint exclusion removed all mesh nodes")
    return np.percentile(stress[:, mask], percentile, axis=1)


def _extract_gmsh_tetrahedra(gmsh: Any) -> tuple[np.ndarray, np.ndarray]:
    node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
    if not len(node_tags):
        raise ValueError("Gmsh produced no nodes")
    order = np.argsort(node_tags)
    sorted_tags = np.asarray(node_tags, dtype=np.int64)[order]
    vertices = np.asarray(coordinates, dtype=np.float64).reshape((-1, 3))[order]
    tag_to_index = {int(tag): index for index, tag in enumerate(sorted_tags)}

    tetra_blocks: list[np.ndarray] = []
    element_types, _, element_node_tags = gmsh.model.mesh.getElements(3)
    for element_type, flat_tags in zip(element_types, element_node_tags):
        _, dimension, order_value, node_count, _, _ = gmsh.model.mesh.getElementProperties(
            element_type
        )
        if dimension == 3 and order_value == 1 and node_count == 4:
            tags = np.asarray(flat_tags, dtype=np.int64).reshape((-1, 4))
            dense = np.fromiter(
                (tag_to_index[int(tag)] for tag in tags.ravel()),
                dtype=np.int64,
                count=tags.size,
            ).reshape((-1, 4))
            tetra_blocks.append(dense)
    if not tetra_blocks:
        raise ValueError("Gmsh produced no first-order tetrahedra")
    tetrahedra = _canonicalize_tetrahedra(vertices, np.vstack(tetra_blocks))
    return vertices, tetrahedra


def _outer_boundary_facets(tetrahedra: np.ndarray) -> np.ndarray:
    faces = np.vstack(
        (
            tetrahedra[:, [0, 1, 2]],
            tetrahedra[:, [0, 1, 3]],
            tetrahedra[:, [0, 2, 3]],
            tetrahedra[:, [1, 2, 3]],
        )
    )
    keys = np.sort(faces, axis=1)
    _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    return faces[counts[inverse] == 1]


def _select_boundary(
    vertices: np.ndarray,
    tetrahedra: np.ndarray,
    selectors: Mapping[str, Callable[[np.ndarray], np.ndarray]],
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    outer = _outer_boundary_facets(tetrahedra)
    nodes: dict[str, np.ndarray] = {}
    facets: dict[str, np.ndarray] = {}
    for name, selector in selectors.items():
        mask = np.asarray(selector(vertices), dtype=bool)
        selected_nodes = np.flatnonzero(mask)
        selected_facets = outer[np.all(mask[outer], axis=1)]
        if not selected_nodes.size or not selected_facets.size:
            raise ValueError(f"Boundary selection {name!r} is empty")
        nodes[name] = selected_nodes
        facets[name] = selected_facets
    return nodes, facets


def _mesh_occ_model(
    name: str,
    mesh_size_m: float,
    geometry: Callable[[Any], None],
) -> tuple[np.ndarray, np.ndarray]:
    if not math.isfinite(mesh_size_m) or mesh_size_m <= 0.0:
        raise ValueError("mesh_size_m must be finite and positive")
    gmsh, _ = _require_fea_dependencies()
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add(name)
        geometry(gmsh)
        gmsh.model.occ.synchronize()
        gmsh.option.setNumber("Mesh.MeshSizeMin", mesh_size_m)
        gmsh.option.setNumber("Mesh.MeshSizeMax", mesh_size_m)
        gmsh.option.setNumber("Mesh.ElementOrder", 1)
        gmsh.option.setNumber("Mesh.Algorithm3D", 1)
        gmsh.model.mesh.generate(3)
        return _extract_gmsh_tetrahedra(gmsh)
    finally:
        gmsh.finalize()


def build_shaft_mesh(inputs: EngineeringInputs, mesh_size_m: float) -> SolverMesh:
    """Build a shaft with a surface load band centered at the chain station."""

    calculate_engineering_loads(inputs)
    radius = inputs.shaft_diameter_m / 2.0
    station = inputs.chain_force_overhang_m
    reaction_station = inputs.shaft_reaction_overhang_m
    band_width = SHAFT_LOAD_BAND_WIDTH_M
    band_start = station - band_width / 2.0
    band_end = station + band_width / 2.0
    reaction_start = reaction_station - band_width / 2.0
    reaction_end = reaction_station + band_width / 2.0

    def geometry(gmsh: Any) -> None:
        breakpoints = sorted(
            {0.0, band_start, band_end, reaction_start, reaction_end, inputs.shaft_length_m}
        )
        segments = []
        for start, end in zip(breakpoints[:-1], breakpoints[1:]):
            segments.append(
                (3, gmsh.model.occ.addCylinder(start, 0.0, 0.0, end - start, 0.0, 0.0, radius))
            )
        gmsh.model.occ.fragment([segments[0]], segments[1:])

    vertices, tetrahedra = _mesh_occ_model("cyclewash-shaft", mesh_size_m, geometry)
    tolerance = max(mesh_size_m * 0.05, 1.0e-9)
    nodes, facets = _select_boundary(
        vertices,
        tetrahedra,
        {
            "fixed": lambda points: np.abs(points[:, 0]) <= tolerance,
            "load": lambda points: (
                np.linalg.norm(points[:, 1:3], axis=1) >= radius - tolerance
            )
            & (points[:, 0] >= band_start - tolerance)
            & (points[:, 0] <= band_end + tolerance),
            "reaction_load": lambda points: (
                np.linalg.norm(points[:, 1:3], axis=1) >= radius - tolerance
            )
            & (points[:, 0] >= reaction_start - tolerance)
            & (points[:, 0] <= reaction_end + tolerance),
        },
    )
    return SolverMesh(
        vertices,
        tetrahedra,
        nodes,
        facets,
        {
            "mesh_size_m": mesh_size_m,
            "axis": "x",
            "component": "shaft",
            "load_station_m": station,
            "reaction_load_station_m": reaction_station,
            "load_band_width_m": band_width,
        },
    )


def _gear_dimensions(inputs: EngineeringInputs) -> tuple[float, float, float]:
    return (
        inputs.gear_sprocket_thickness_m,
        inputs.gear_hub_radius_m,
        inputs.gear_hub_thickness_m,
    )


def build_gear_hub_mesh(inputs: EngineeringInputs, mesh_size_m: float) -> SolverMesh:
    """Build a fused solid disc and hub without explicit teeth."""

    calculate_engineering_loads(inputs)
    disc_thickness, hub_radius, hub_thickness = _gear_dimensions(inputs)
    hub_start = 0.5 * (disc_thickness - hub_thickness)

    def geometry(gmsh: Any) -> None:
        disc = gmsh.model.occ.addCylinder(
            0.0, 0.0, 0.0, disc_thickness, 0.0, 0.0, inputs.gear_pitch_radius_m
        )
        hub = gmsh.model.occ.addCylinder(
            hub_start, 0.0, 0.0, hub_thickness, 0.0, 0.0, hub_radius
        )
        fused, _ = gmsh.model.occ.fuse(
            [(3, disc)], [(3, hub)], removeObject=True, removeTool=True
        )
        bore_radius = inputs.shaft_diameter_m / 2.0
        bore = gmsh.model.occ.addCylinder(
            hub_start - 1.0e-6,
            0.0,
            0.0,
            hub_thickness + 2.0e-6,
            0.0,
            0.0,
            bore_radius,
        )
        gmsh.model.occ.cut(fused, [(3, bore)], removeObject=True, removeTool=True)

    vertices, tetrahedra = _mesh_occ_model("cyclewash-gear", mesh_size_m, geometry)
    radial = np.linalg.norm(vertices[:, 1:3], axis=1)
    tolerance = max(mesh_size_m * 0.12, 1.0e-8)
    bore_radius = inputs.shaft_diameter_m / 2.0
    nodes, facets = _select_boundary(
        vertices,
        tetrahedra,
        {
            "hub_interface": lambda points: np.abs(
                np.linalg.norm(points[:, 1:3], axis=1) - bore_radius
            )
            <= tolerance,
            "pitch_load": lambda points: np.linalg.norm(points[:, 1:3], axis=1)
            >= inputs.gear_pitch_radius_m - tolerance,
        },
    )
    return SolverMesh(
        vertices,
        tetrahedra,
        nodes,
        facets,
        {
            "mesh_size_m": mesh_size_m,
            "component": "gear",
            "disc_thickness_m": disc_thickness,
            "hub_radius_m": hub_radius,
            "hub_thickness_m": hub_thickness,
            "bore_radius_m": bore_radius,
        },
    )


def circular_segment_fill_fraction(radius_m: float, water_surface_z: float) -> float:
    """Return the circular cross-section area fraction below a waterline."""

    if not math.isfinite(radius_m) or radius_m <= 0.0:
        raise ValueError("radius_m must be finite and positive")
    if not math.isfinite(water_surface_z):
        raise ValueError("water_surface_z must be finite")
    z = min(max(water_surface_z, -radius_m), radius_m)
    height = z + radius_m
    if height <= 0.0:
        return 0.0
    if height >= 2.0 * radius_m:
        return 1.0
    offset = radius_m - height
    area = radius_m**2 * math.acos(offset / radius_m) - offset * math.sqrt(
        max(0.0, 2.0 * radius_m * height - height**2)
    )
    return area / (math.pi * radius_m**2)


def water_surface_z_m(radius_m: float, fill_fraction: float) -> float:
    """Invert circular-segment area to a gravity-level horizontal waterline."""

    if not math.isfinite(fill_fraction) or not 0.0 <= fill_fraction <= 1.0:
        raise ValueError("fill_fraction must be between 0 and 1")
    if not math.isfinite(radius_m) or radius_m <= 0.0:
        raise ValueError("radius_m must be finite and positive")
    if fill_fraction == 0.0:
        return -radius_m
    if fill_fraction == 1.0:
        return radius_m
    low, high = -radius_m, radius_m
    for _ in range(64):
        middle = 0.5 * (low + high)
        if circular_segment_fill_fraction(radius_m, middle) < fill_fraction:
            low = middle
        else:
            high = middle
    return 0.5 * (low + high)


def drum_pressure_ceiling_pa(inputs: EngineeringInputs, analytical: Any) -> float:
    """Return the pressure ceiling using circular-segment water depth."""

    inner_radius = inputs.drum_radius_m - inputs.drum_wall_thickness_m
    water_depth = water_surface_z_m(inner_radius, inputs.fill_fraction) + inner_radius
    hydrostatic = inputs.water_density_kg_m3 * inputs.gravity_m_s2 * water_depth
    return hydrostatic * inputs.slosh_amplification + analytical.centrifugal_pressure_pa


def slosh_lobe_at_points(points: np.ndarray, phase_deg: float) -> np.ndarray:
    """Evaluate the unit raised-cosine slosh lobe around the drum axis."""

    points = np.asarray(points, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
        raise ValueError("points must have finite shape (point_count, 3)")
    if not math.isfinite(phase_deg):
        raise ValueError("phase_deg must be finite")
    angle = np.arctan2(points[:, 2], points[:, 1])
    return 0.5 * (1.0 + np.cos(angle - math.radians(phase_deg)))


def build_drum_mesh(inputs: EngineeringInputs, mesh_size_m: float) -> SolverMesh:
    """Build a radially partitioned equivalent drum, back plate, and boss."""

    analytical = calculate_engineering_loads(inputs)
    outer_radius = inputs.drum_radius_m
    inner_radius = outer_radius - inputs.drum_wall_thickness_m
    if inner_radius <= 0.0:
        raise ValueError("drum_wall_thickness_m must be smaller than drum_radius_m")
    wall = inputs.drum_wall_thickness_m
    middle_radius = inner_radius + wall / 2.0
    boss_radius = max(3.2 * inputs.shaft_diameter_m, 0.25 * outer_radius)
    boss_depth = min(max(5.0 * wall, 0.015), 0.2 * inputs.drum_depth_m)
    waterline = water_surface_z_m(inner_radius, inputs.fill_fraction)

    def geometry(gmsh: Any) -> None:
        def ring(inner: float, outer: float) -> list[tuple[int, int]]:
            outer_tag = gmsh.model.occ.addCylinder(
                wall, 0.0, 0.0, inputs.drum_depth_m - wall, 0.0, 0.0, outer
            )
            inner_tag = gmsh.model.occ.addCylinder(
                wall, 0.0, 0.0, inputs.drum_depth_m - wall, 0.0, 0.0, inner
            )
            result, _ = gmsh.model.occ.cut(
                [(3, outer_tag)], [(3, inner_tag)], removeObject=True, removeTool=True
            )
            return result

        inner_layer = ring(inner_radius, middle_radius)
        outer_layer = ring(middle_radius, outer_radius)
        back = gmsh.model.occ.addCylinder(
            0.0, 0.0, 0.0, wall, 0.0, 0.0, outer_radius
        )
        boss = gmsh.model.occ.addCylinder(
            wall, 0.0, 0.0, boss_depth - wall, 0.0, 0.0, boss_radius
        )
        volumes = inner_layer + outer_layer + [(3, back), (3, boss)]
        gmsh.model.occ.fragment([volumes[0]], volumes[1:])

    # The two radial volume partitions force three nodal sampling surfaces across
    # the 3 mm equivalent wall. The nominal mesh size controls only in-plane size.
    effective_mesh_size = min(mesh_size_m, 0.045)
    vertices, tetrahedra = _mesh_occ_model(
        "cyclewash-drum", effective_mesh_size, geometry
    )
    tolerance = max(wall * 0.30, 2.0e-7)
    radial = np.linalg.norm(vertices[:, 1:3], axis=1)
    selectors = {
        "coupling": lambda points: (np.abs(points[:, 0]) <= tolerance)
        & (np.linalg.norm(points[:, 1:3], axis=1) <= boss_radius + tolerance),
        "internal_pressure": lambda points: (
            np.abs(np.linalg.norm(points[:, 1:3], axis=1) - inner_radius)
            <= tolerance
        )
        & (points[:, 0] >= wall - tolerance),
    }
    if analytical.retained_water_weight_n > 0.0:
        selectors["wetted_back"] = (
            lambda points: (np.abs(points[:, 0] - wall) <= tolerance)
            & (np.linalg.norm(points[:, 1:3], axis=1) >= boss_radius - tolerance)
            & (np.linalg.norm(points[:, 1:3], axis=1) <= inner_radius + tolerance)
            & (points[:, 2] <= waterline + tolerance)
        )
    nodes, facets = _select_boundary(vertices, tetrahedra, selectors)
    return SolverMesh(
        vertices,
        tetrahedra,
        nodes,
        facets,
        {
            "mesh_size_m": mesh_size_m,
            "effective_in_plane_mesh_size_m": effective_mesh_size,
            "component": "drum",
            "inner_radius_m": inner_radius,
            "middle_radius_m": middle_radius,
            "wall_thickness_m": wall,
            "radial_layer_count": 2,
            "boss_radius_m": boss_radius,
            "effective_youngs_modulus_pa": inputs.drum_material.youngs_modulus_pa
            * inputs.drum_stiffness_factor,
        },
    )


def drum_pressure_at_points(
    points: np.ndarray,
    phase_deg: float,
    inputs: EngineeringInputs,
    analytical: Any,
) -> np.ndarray:
    """Evaluate bounded hydrostatic, centrifugal, and smooth slosh pressure."""

    points = np.asarray(points, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
        raise ValueError("points must have finite shape (point_count, 3)")
    if not math.isfinite(phase_deg):
        raise ValueError("phase_deg must be finite")
    inner_radius = inputs.drum_radius_m - inputs.drum_wall_thickness_m
    free_surface = water_surface_z_m(inner_radius, inputs.fill_fraction)
    depth = np.maximum(free_surface - points[:, 2], 0.0)
    wet = depth > 0.0
    hydrostatic = inputs.water_density_kg_m3 * inputs.gravity_m_s2 * depth
    # Raised cosine gives a smooth, bounded lobe centered on the rotating phase.
    lobe = slosh_lobe_at_points(points, phase_deg)
    amplification = 1.0 + (inputs.slosh_amplification - 1.0) * lobe
    pressure = (hydrostatic + analytical.centrifugal_pressure_pa) * amplification
    pressure[~wet] = 0.0
    return np.clip(pressure, 0.0, drum_pressure_ceiling_pa(inputs, analytical))


def _facet_areas(vertices: np.ndarray, facets: np.ndarray) -> np.ndarray:
    triangles = vertices[facets]
    return 0.5 * np.linalg.norm(
        np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]),
        axis=1,
    )


def integrate_surface_traction(
    vertices: np.ndarray,
    facets: np.ndarray,
    traction_function: Callable[[np.ndarray], np.ndarray],
) -> dict[str, Any]:
    """Integrate force and moment using degree-two triangle quadrature."""

    triangles = np.asarray(vertices, dtype=np.float64)[np.asarray(facets, dtype=np.int64)]
    areas = _facet_areas(vertices, facets)
    barycentric = np.array(
        [[2.0 / 3.0, 1.0 / 6.0, 1.0 / 6.0],
         [1.0 / 6.0, 2.0 / 3.0, 1.0 / 6.0],
         [1.0 / 6.0, 1.0 / 6.0, 2.0 / 3.0]]
    )
    points = np.einsum("qa,fad->fqd", barycentric, triangles)
    flat_points = points.reshape((-1, 3))
    traction = np.asarray(traction_function(flat_points), dtype=np.float64).reshape(
        points.shape
    )
    weights = np.repeat((areas / 3.0)[:, None], 3, axis=1)
    force = np.sum(traction * weights[:, :, None], axis=(0, 1))
    moment = np.sum(np.cross(points, traction) * weights[:, :, None], axis=(0, 1))
    return {"force_n": force, "moment_n_m": moment, "area_m2": float(areas.sum())}


def _surface_integral(
    vertices: np.ndarray,
    facets: np.ndarray,
    scalar_function: Callable[[np.ndarray], np.ndarray],
) -> float:
    integrated = integrate_surface_traction(
        vertices,
        facets,
        lambda points: np.column_stack(
            (np.asarray(scalar_function(points), dtype=np.float64), np.zeros((points.shape[0], 2)))
        ),
    )
    return float(integrated["force_n"][0])


def _shaft_traction_function(
    inputs: EngineeringInputs, mesh: SolverMesh
) -> tuple[Callable[[np.ndarray], np.ndarray], dict[str, float]]:
    analytical = calculate_engineering_loads(inputs)
    facets = mesh.boundary_facets["load"]
    area = float(_facet_areas(mesh.vertices_m, facets).sum())
    polar_integral = _surface_integral(
        mesh.vertices_m, facets, lambda points: points[:, 1] ** 2 + points[:, 2] ** 2
    )
    force_density = analytical.chain_force_n / area
    torque_coefficient = analytical.design_torque_n_m / polar_integral

    def traction(points: np.ndarray) -> np.ndarray:
        return np.column_stack(
            (
                np.zeros(points.shape[0]),
                -torque_coefficient * points[:, 2],
                torque_coefficient * points[:, 1] - force_density,
            )
        )

    return traction, {
        "force_traction_pa": force_density,
        "torque_coefficient_pa_per_m": torque_coefficient,
    }


def _shaft_reaction_traction_function(
    inputs: EngineeringInputs, mesh: SolverMesh
) -> Callable[[np.ndarray], np.ndarray]:
    facets = mesh.boundary_facets["reaction_load"]
    area = float(_facet_areas(mesh.vertices_m, facets).sum())
    traction_z = -inputs.shaft_transverse_reaction_n / area

    def traction(points: np.ndarray) -> np.ndarray:
        values = np.zeros_like(points)
        values[:, 2] = traction_z
        return values

    return traction


def shaft_load_resultants(inputs: EngineeringInputs, mesh: SolverMesh) -> dict[str, float]:
    """Numerically integrate the actual shaft-band traction function."""

    facets = mesh.boundary_facets["load"]
    traction, coefficients = _shaft_traction_function(inputs, mesh)
    integrated = integrate_surface_traction(mesh.vertices_m, facets, traction)
    reaction_traction = _shaft_reaction_traction_function(inputs, mesh)
    reaction_integrated = integrate_surface_traction(
        mesh.vertices_m, mesh.boundary_facets["reaction_load"], reaction_traction
    )
    chain_moment = float(abs(integrated["moment_n_m"][1]))
    reaction_moment = float(abs(reaction_integrated["moment_n_m"][1]))
    return {
        "chain_force_n": float(abs(integrated["force_n"][2])),
        "reaction_force_n": float(abs(reaction_integrated["force_n"][2])),
        "total_transverse_force_n": float(
            abs(integrated["force_n"][2]) + abs(reaction_integrated["force_n"][2])
        ),
        "torque_n_m": float(abs(integrated["moment_n_m"][0])),
        "chain_bending_moment_n_m": chain_moment,
        "reaction_bending_moment_n_m": reaction_moment,
        "bending_moment_n_m": chain_moment + reaction_moment,
        "load_area_m2": integrated["area_m2"],
        **coefficients,
    }


def gear_load_resultants(inputs: EngineeringInputs, mesh: SolverMesh) -> dict[str, float]:
    """Numerically integrate the actual gear pitch-region traction."""

    facets = mesh.boundary_facets["pitch_load"]
    traction, coefficient = _gear_traction_function(inputs, mesh)
    integrated = integrate_surface_traction(mesh.vertices_m, facets, traction)
    return {
        "torque_n_m": float(abs(integrated["moment_n_m"][0])),
        "force_n": integrated["force_n"],
        "coefficient": coefficient,
    }


def _gear_traction_function(
    inputs: EngineeringInputs, mesh: SolverMesh
) -> tuple[Callable[[np.ndarray], np.ndarray], float]:
    analytical = calculate_engineering_loads(inputs)
    facets = mesh.boundary_facets["pitch_load"]
    polar_integral = _surface_integral(
        mesh.vertices_m, facets, lambda points: points[:, 1] ** 2 + points[:, 2] ** 2
    )
    coefficient = analytical.design_torque_n_m / polar_integral

    def traction(points: np.ndarray) -> np.ndarray:
        return coefficient * np.column_stack(
            (np.zeros(points.shape[0]), -points[:, 2], points[:, 1])
        )

    return traction, coefficient


def _drum_pressure_traction_function(
    inputs: EngineeringInputs, phase_deg: float
) -> Callable[[np.ndarray], np.ndarray]:
    analytical = calculate_engineering_loads(inputs)

    def traction(points: np.ndarray) -> np.ndarray:
        pressure = drum_pressure_at_points(points, phase_deg, inputs, analytical)
        radius = np.linalg.norm(points[:, 1:3], axis=1)
        radial = np.zeros_like(points)
        nonzero = radius > 0.0
        radial[nonzero, 1] = points[nonzero, 1] / radius[nonzero]
        radial[nonzero, 2] = points[nonzero, 2] / radius[nonzero]
        return pressure[:, None] * radial

    return traction


def _drum_weight_traction_function(
    inputs: EngineeringInputs, mesh: SolverMesh
) -> Callable[[np.ndarray], np.ndarray]:
    analytical = calculate_engineering_loads(inputs)
    area = float(_facet_areas(mesh.vertices_m, mesh.boundary_facets["wetted_back"]).sum())
    if area <= 0.0:
        raise ValueError("wetted_back area must be positive")
    traction_z = -analytical.retained_water_weight_n / area

    def traction(points: np.ndarray) -> np.ndarray:
        values = np.zeros_like(points)
        values[:, 2] = traction_z
        return values

    return traction


def drum_load_resultants(
    inputs: EngineeringInputs, mesh: SolverMesh, phase_deg: float
) -> dict[str, Any]:
    """Integrate side pressure and retained-water gravity tractions."""

    pressure = integrate_surface_traction(
        mesh.vertices_m,
        mesh.boundary_facets["internal_pressure"],
        _drum_pressure_traction_function(inputs, phase_deg),
    )
    if "wetted_back" in mesh.boundary_facets:
        weight = integrate_surface_traction(
            mesh.vertices_m,
            mesh.boundary_facets["wetted_back"],
            _drum_weight_traction_function(inputs, mesh),
        )
    else:
        if calculate_engineering_loads(inputs).retained_water_weight_n > 0.0:
            raise ValueError("wetted_back boundary is required for retained water")
        weight = {
            "force_n": np.zeros(3, dtype=np.float64),
            "moment_n_m": np.zeros(3, dtype=np.float64),
            "area_m2": 0.0,
        }
    return {
        "pressure_force_n": pressure["force_n"],
        "pressure_moment_n_m": pressure["moment_n_m"],
        "weight_force_n": weight["force_n"],
        "weight_moment_n_m": weight["moment_n_m"],
        "wetted_back_area_m2": weight["area_m2"],
    }


def _solve_elasticity(
    mesh: SolverMesh,
    fixed_region: str,
    load_tractions: Mapping[str, Callable[[np.ndarray], np.ndarray]],
    youngs_modulus_pa: float,
    poisson_ratio: float,
    yield_strength_pa: float,
) -> _ElasticityFields:
    """Solve isotropic elasticity with one or more surface traction regions."""

    _require_fea_dependencies()
    from sfepy.base.base import IndexedStruct, output

    output.set_output(quiet=True)
    # SfePy probes optional JAX terms during its first discrete import and emits
    # an installation hint with print(). JAX is not used here, so keep the CLI
    # stdout limited to its machine-readable progress protocol.
    with redirect_stdout(StringIO()):
        from sfepy.discrete import (
            Equation,
            Equations,
            FieldVariable,
            Function,
            Functions,
            Integral,
            Material,
            Problem,
        )
        from sfepy.discrete.conditions import Conditions, EssentialBC
        from sfepy.discrete.fem import FEDomain, Field, Mesh
        from sfepy.mechanics.matcoefs import stiffness_from_youngpoisson
        from sfepy.solvers.ls import ScipyDirect
        from sfepy.solvers.nls import Newton
        from sfepy.terms import Term

    sfepy_mesh = Mesh.from_data(
        "cyclewash",
        mesh.vertices_m,
        np.zeros(mesh.vertices_m.shape[0], dtype=np.int32),
        [mesh.tetrahedra],
        [np.zeros(mesh.tetrahedra.shape[0], dtype=np.int32)],
        ["3_4"],
    )
    domain = FEDomain("domain", sfepy_mesh)
    omega = domain.create_region("Omega", "all")

    def make_selector(indices: np.ndarray) -> Callable[..., np.ndarray]:
        selected = np.asarray(indices, dtype=np.int64)

        def selector(coors: np.ndarray, domain: Any = None) -> np.ndarray:
            del coors, domain
            return selected

        return selector

    selector_functions = [
        Function("select_fixed", make_selector(mesh.boundary_nodes[fixed_region]))
    ]
    for index, region_name in enumerate(load_tractions):
        selector_functions.append(
            Function(
                f"select_load_{index}", make_selector(mesh.boundary_nodes[region_name])
            )
        )
    functions = Functions(selector_functions)
    fixed = domain.create_region(
        "Fixed", "vertices by select_fixed", "facet", functions=functions
    )
    loaded_regions = []
    for index, region_name in enumerate(load_tractions):
        loaded_regions.append(
            domain.create_region(
                f"Loaded{index}",
                f"vertices by select_load_{index}",
                "facet",
                functions=functions,
            )
        )
    if fixed.vertices.size == 0 or any(region.vertices.size == 0 for region in loaded_regions):
        raise ValueError("SfePy boundary region selection is empty")

    field = Field.from_args("displacement", np.float64, "vector", omega, approx_order=1)
    displacement = FieldVariable("u", "unknown", field)
    test = FieldVariable("v", "test", field, primary_var_name="u")
    stiffness = stiffness_from_youngpoisson(3, youngs_modulus_pa, poisson_ratio)
    solid = Material("solid", D=stiffness)

    integral = Integral("i", order=2)
    stiffness_term = Term.new(
        "dw_lin_elastic(solid.D, v, u)",
        integral,
        omega,
        solid=solid,
        v=test,
        u=displacement,
    )
    balance = stiffness_term
    for index, ((_, traction_function), loaded_region) in enumerate(
        zip(load_tractions.items(), loaded_regions)
    ):
        def load_material(
            ts: Any,
            coors: np.ndarray,
            mode: str | None = None,
            _traction: Callable[[np.ndarray], np.ndarray] = traction_function,
            **kwargs: Any,
        ):
            del ts, kwargs
            if mode == "qp":
                values = np.asarray(_traction(coors), dtype=np.float64)
                if values.shape != coors.shape:
                    raise ValueError("traction function must return shape (point_count, 3)")
                return {"val": values[:, :, None]}
            return None

        load = Material(
            f"load{index}",
            function=Function(f"load_material_{index}", load_material),
        )
        traction_term = Term.new(
            f"dw_surface_ltr(load{index}.val, v)",
            integral,
            loaded_region,
            v=test,
            **{f"load{index}": load},
        )
        balance = balance - traction_term
    equations = Equations([Equation("equilibrium", balance)])
    problem = Problem("cyclewash-elasticity", equations=equations)
    problem.set_bcs(ebcs=Conditions([EssentialBC("fixed", fixed, {"u.all": 0.0})]))
    linear_solver = ScipyDirect({"use_presolve": True})
    nonlinear_solver = Newton(
        {"i_max": 1, "eps_a": 1.0e-10, "eps_r": 1.0e-10},
        lin_solver=linear_solver,
    )
    problem.set_solver(nonlinear_solver)
    status = IndexedStruct()
    state = problem.solve(status=status, save_results=False, verbose=False)
    if getattr(status, "condition", 0) not in (0, None):
        raise RuntimeError(f"SfePy solve failed with condition {status.condition}")
    nodal_displacement = state.get_state_parts()["u"].reshape((-1, 3))
    element_stress = problem.evaluate(
        "ev_cauchy_stress.2.Omega(solid.D, u)",
        mode="el_avg",
        solid=solid,
        u=state["u"],
        verbose=False,
    )
    stress_voigt = np.asarray(element_stress).reshape((-1, 6))
    element_strain = problem.evaluate(
        "ev_cauchy_strain.2.Omega(u)",
        mode="el_avg",
        u=state["u"],
        verbose=False,
    )
    strain_voigt = np.asarray(element_strain).reshape((-1, 6))
    nodal_stress = volume_weighted_nodal_stress(
        mesh.vertices_m, mesh.tetrahedra, stress_voigt
    )
    nodal_vm = _von_mises_from_voigt(nodal_stress)
    nodal_maximum_shear = maximum_shear_from_stress_voigt(nodal_stress)
    nodal_fos = nodal_factor_of_safety(nodal_vm, yield_strength_pa)
    recovered = (nodal_displacement, nodal_vm, strain_voigt, nodal_maximum_shear, nodal_fos)
    if not all(np.isfinite(field).all() for field in recovered):
        raise RuntimeError("SfePy returned non-finite fields")
    return _ElasticityFields(*recovered)


def solve_shaft_case(inputs: EngineeringInputs, mesh_level: str) -> ComponentFieldResult:
    """Solve shaft torque and chain force at the specified overhang station."""

    mesh_size = _mesh_size("shaft", mesh_level)
    mesh = build_shaft_mesh(inputs, mesh_size)
    traction, _ = _shaft_traction_function(inputs, mesh)
    reaction_traction = _shaft_reaction_traction_function(inputs, mesh)

    recovered = _solve_elasticity(
        mesh,
        "fixed",
        {"load": traction, "reaction_load": reaction_traction},
        inputs.shaft_material.youngs_modulus_pa,
        inputs.shaft_material.poisson_ratio,
        inputs.shaft_material.yield_strength_pa,
    )
    return ComponentFieldResult(
        mesh.vertices_m,
        mesh.tetrahedra,
        recovered.displacement_m[None, :, :],
        recovered.von_mises_pa[None, :],
        (0.0,),
        element_strain=recovered.element_strain[None, :, :],
        nodal_maximum_shear_pa=recovered.nodal_maximum_shear_pa[None, :],
        nodal_factor_of_safety=recovered.nodal_factor_of_safety[None, :],
    )


def solve_gear_case(inputs: EngineeringInputs, mesh_level: str) -> ComponentFieldResult:
    """Solve torque transfer from the pitch surface into the fixed hub face."""

    mesh = build_gear_hub_mesh(inputs, _mesh_size("gear", mesh_level))
    traction, _ = _gear_traction_function(inputs, mesh)

    recovered = _solve_elasticity(
        mesh,
        "hub_interface",
        {"pitch_load": traction},
        inputs.shaft_material.youngs_modulus_pa,
        inputs.shaft_material.poisson_ratio,
        inputs.shaft_material.yield_strength_pa,
    )
    return ComponentFieldResult(
        mesh.vertices_m,
        mesh.tetrahedra,
        recovered.displacement_m[None, :, :],
        recovered.von_mises_pa[None, :],
        (0.0,),
        element_strain=recovered.element_strain[None, :, :],
        nodal_maximum_shear_pa=recovered.nodal_maximum_shear_pa[None, :],
        nodal_factor_of_safety=recovered.nodal_factor_of_safety[None, :],
    )


def solve_drum_phases(
    inputs: EngineeringInputs,
    mesh_level: str,
    progress_callback: Callable[[int, int], None] | None = None,
) -> ComponentFieldResult:
    """Solve twelve pressure-plus-retained-water gravity load phases."""

    mesh = build_drum_mesh(inputs, _mesh_size("drum", mesh_level))
    displacements: list[np.ndarray] = []
    stresses: list[np.ndarray] = []
    strains: list[np.ndarray] = []
    maximum_shears: list[np.ndarray] = []
    factors_of_safety: list[np.ndarray] = []
    analytical = calculate_engineering_loads(inputs)
    weight_traction = (
        _drum_weight_traction_function(inputs, mesh)
        if analytical.retained_water_weight_n > 0.0
        else None
    )
    for phase_index, phase in enumerate(PHASE_DEGREES, start=1):
        load_tractions = {
            "internal_pressure": _drum_pressure_traction_function(inputs, phase)
        }
        if weight_traction is not None:
            load_tractions["wetted_back"] = weight_traction
        recovered = _solve_elasticity(
            mesh,
            "coupling",
            load_tractions,
            mesh.metadata["effective_youngs_modulus_pa"],
            inputs.drum_material.poisson_ratio,
            inputs.drum_material.yield_strength_pa,
        )
        displacements.append(recovered.displacement_m)
        stresses.append(recovered.von_mises_pa)
        strains.append(recovered.element_strain)
        maximum_shears.append(recovered.nodal_maximum_shear_pa)
        factors_of_safety.append(recovered.nodal_factor_of_safety)
        if progress_callback is not None:
            progress_callback(phase_index, len(PHASE_DEGREES))
    return ComponentFieldResult(
        mesh.vertices_m,
        mesh.tetrahedra,
        np.stack(displacements),
        np.stack(stresses),
        PHASE_DEGREES,
        element_strain=np.stack(strains),
        nodal_maximum_shear_pa=np.stack(maximum_shears),
        nodal_factor_of_safety=np.stack(factors_of_safety),
    )


def solve_cantilever_benchmark(mesh_level: str = "medium") -> dict[str, float]:
    """Solve a rectangular cantilever and compare its mean tip deflection."""

    sizes = {"coarse": 0.0040, "medium": 0.0020, "fine": 0.0015}
    if mesh_level not in sizes:
        raise ValueError("mesh_level must be coarse, medium, or fine")
    length = 0.100
    width = 0.010
    height = 0.010
    force_n = 100.0
    youngs_modulus = 200.0e9

    def geometry(gmsh: Any) -> None:
        gmsh.model.occ.addBox(0.0, -width / 2.0, -height / 2.0, length, width, height)

    vertices, tetrahedra = _mesh_occ_model("cantilever", sizes[mesh_level], geometry)
    tolerance = sizes[mesh_level] * 0.05
    nodes, facets = _select_boundary(
        vertices,
        tetrahedra,
        {
            "fixed": lambda points: np.abs(points[:, 0]) <= tolerance,
            "load": lambda points: np.abs(points[:, 0] - length) <= tolerance,
        },
    )
    mesh = SolverMesh(vertices, tetrahedra, nodes, facets, {"mesh_size_m": sizes[mesh_level]})
    area = float(_facet_areas(vertices, facets["load"]).sum())

    def traction(points: np.ndarray) -> np.ndarray:
        values = np.zeros_like(points)
        values[:, 2] = -force_n / area
        return values

    recovered = _solve_elasticity(
        mesh, "fixed", {"load": traction}, youngs_modulus, 0.30, 250.0e6
    )
    fea_tip = float(abs(np.mean(recovered.displacement_m[nodes["load"], 2])))
    second_moment = width * height**3 / 12.0
    analytical_tip = force_n * length**3 / (3.0 * youngs_modulus * second_moment)
    return {
        "fea_tip_displacement_m": fea_tip,
        "analytical_tip_displacement_m": analytical_tip,
        "relative_error": abs(fea_tip - analytical_tip) / analytical_tip,
        "node_count": float(vertices.shape[0]),
        "element_count": float(tetrahedra.shape[0]),
    }


def _mesh_size(component: str, mesh_level: str) -> float:
    try:
        return MESH_SIZES_M[component][mesh_level]
    except KeyError as error:
        raise ValueError("mesh_level must be coarse, medium, or fine") from error


def unconstrained_stress_percentile(
    result: ComponentFieldResult,
    component: str,
    inputs: EngineeringInputs,
    mesh_level: str,
    percentile: float = 95.0,
) -> float:
    """Return the worst per-phase P95 away from actual constrained nodes."""

    mesh_size = _mesh_size(component, mesh_level)
    vertices = result.vertices_m
    tolerance = max(mesh_size * 0.05, 1.0e-8)
    radial = np.linalg.norm(vertices[:, 1:3], axis=1)
    if component == "shaft":
        constrained = np.flatnonzero(np.abs(vertices[:, 0] - vertices[:, 0].min()) <= tolerance)
    elif component == "gear":
        constrained = np.flatnonzero(
            np.abs(radial - inputs.shaft_diameter_m / 2.0) <= tolerance
        )
    elif component == "drum":
        boss_radius = max(3.2 * inputs.shaft_diameter_m, 0.25 * inputs.drum_radius_m)
        constrained = np.flatnonzero(
            (np.abs(vertices[:, 0] - vertices[:, 0].min()) <= tolerance)
            & (radial <= boss_radius + tolerance)
        )
    else:
        raise ValueError("component must be shaft, gear, or drum")
    per_phase = unconstrained_phase_stress_percentiles(
        vertices,
        result.von_mises_pa,
        constrained,
        mesh_size,
        percentile,
    )
    return float(per_phase.max())


def _metrics(
    result: ComponentFieldResult,
    component: str,
    inputs: EngineeringInputs,
    mesh_level: str,
) -> MeshMetrics:
    return MeshMetrics(
        stress_95th_percentile_pa=unconstrained_stress_percentile(
            result, component, inputs, mesh_level
        ),
        maximum_displacement_m=float(np.max(np.linalg.norm(result.displacement_m, axis=2))),
        node_count=int(result.vertices_m.shape[0]),
        element_count=int(result.tetrahedra.shape[0]),
    )


def evaluate_component_convergence(
    medium: MeshMetrics, fine: MeshMetrics, tolerance: float = 0.10
) -> MeshConvergenceResult:
    """Dependency-light public convergence helper."""

    return evaluate_mesh_convergence(medium, fine, tolerance)


def run_mesh_convergence(inputs: EngineeringInputs) -> dict[str, MeshConvergenceResult]:
    """Run medium/fine shaft, gear, and drum sensitivity comparisons."""

    solvers = {
        "shaft": solve_shaft_case,
        "gear": solve_gear_case,
        "drum": solve_drum_phases,
    }
    convergence: dict[str, MeshConvergenceResult] = {}
    for component, solve in solvers.items():
        medium_result = solve(inputs, "medium")
        fine_result = solve(inputs, "fine")
        convergence[component] = evaluate_component_convergence(
            _metrics(medium_result, component, inputs, "medium"),
            _metrics(fine_result, component, inputs, "fine"),
        )
    return convergence


def _material_from_mapping(value: Mapping[str, Any], field_name: str) -> MaterialProperties:
    try:
        return MaterialProperties(**value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid {field_name}: {error}") from error


def engineering_inputs_from_mapping(value: Mapping[str, Any]) -> EngineeringInputs:
    """Parse the Task 6 JSON representation into immutable engineering inputs."""

    if not isinstance(value, Mapping):
        raise ValueError("input JSON must contain an object")
    allowed = {item.name for item in fields(EngineeringInputs)}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"Unsupported engineering input fields: {sorted(unknown)}")
    normalized = dict(value)
    for name in ("shaft_material", "drum_material"):
        if name in normalized:
            if not isinstance(normalized[name], Mapping):
                raise ValueError(f"{name} must be an object")
            normalized[name] = _material_from_mapping(normalized[name], name)
    try:
        inputs = EngineeringInputs(**normalized)
    except TypeError as error:
        raise ValueError(f"Invalid engineering inputs: {error}") from error
    calculate_engineering_loads(inputs)
    return inputs


def solve_stage1_package(
    inputs: EngineeringInputs,
    mesh_level: str,
    include_convergence: bool = False,
) -> Stage1FeaPackage:
    """Solve all Stage 1 components and construct the versioned package."""

    gmsh, sfepy = _require_fea_dependencies()
    import meshio

    analytical = calculate_engineering_loads(inputs)
    requested_levels = normalize_mesh_levels(
        (mesh_level, "medium", "fine") if include_convergence else (mesh_level,)
    )
    shaft = solve_shaft_case(inputs, mesh_level)
    gear = solve_gear_case(inputs, mesh_level)
    drum = solve_drum_phases(inputs, mesh_level)
    convergence = run_mesh_convergence(inputs) if include_convergence else {}
    return Stage1FeaPackage(
        schema_version=SCHEMA_VERSION,
        shaft=shaft,
        gear=gear,
        drum=drum,
        solver_versions={
            "gmsh": str(gmsh.__version__),
            "sfepy": str(sfepy.__version__),
            "meshio": str(meshio.__version__),
            "numpy": str(np.__version__),
        },
        assumptions={
            "analysis": "Linear-static FEA with reduced-order water loading; not CFD.",
            "request_identity": canonical_request_identity(inputs, requested_levels),
            "load_model_limitations": (
                "Reduced-order side pressure and retained-water back traction; not CFD or fluid-structure interaction"
            ),
            "convergence_policy": (
                "Medium/fine worst-phase unconstrained P95 stress and maximum displacement must each change by less than 10%; otherwise sensitivity is unresolved"
            ),
            "safety_factor_policy": (
                f"Yield strength divided by nodal von Mises stress; zero-stress values capped at {FACTOR_OF_SAFETY_CAP:.0e}"
            ),
            "symmetric_tensor_order": ",".join(SYMMETRIC_TENSOR_COMPONENTS),
            "drum_wall_model": (
                "Equivalent hollow solid with two forced radial layers and no explicit perforations"
            ),
            "drum_gravity_load": (
                "Retained-water weight is distributed over the gravity-wetted internal back region"
            ),
            "drum_solid_tet_limitation": (
                "Two forced radial layers improve thickness sampling but do not imply shell convergence"
            ),
            "gear_model": "Toothless disc and hub with a cylindrical shaft bore constraint",
            "shaft_load_station_m": inputs.chain_force_overhang_m,
            "shaft_reaction_load_n": inputs.shaft_transverse_reaction_n,
            "shaft_reaction_station_m": inputs.shaft_reaction_overhang_m,
            "enclosure_scope": "Enclosure dimensions are reported inputs and are not solved",
            "mesh_level": mesh_level,
            "units": "SI: m, N, Pa, degrees",
        },
        convergence=convergence,
        inputs=inputs.to_dict(),
        analytical_values=asdict(analytical),
    )


def _progress(fraction: float, message: str) -> None:
    print(f"PROGRESS {fraction:.2f} {message}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--mesh-level",
        choices=("coarse", "medium", "fine"),
        action="append",
        dest="mesh_levels",
    )
    parser.add_argument("--convergence", action="store_true")
    args = parser.parse_args(argv)
    try:
        raw = json.loads(args.input.read_text(encoding="utf-8"))
        inputs = engineering_inputs_from_mapping(raw)
        analytical = calculate_engineering_loads(inputs)
        levels = normalize_mesh_levels(args.mesh_levels or ("coarse",))
        selected_level = levels[-1]
        shaft = solve_shaft_case(inputs, selected_level)
        _progress(0.30, "shaft solve complete")
        gear = solve_gear_case(inputs, selected_level)
        _progress(0.45, "gear solve complete")

        def report_drum_progress(completed: int, total: int) -> None:
            fraction = 0.45 + 0.35 * completed / total
            _progress(fraction, f"drum phase {completed}/{total}")

        drum = solve_drum_phases(inputs, selected_level, report_drum_progress)
        convergence_requested = args.convergence or {"medium", "fine"}.issubset(levels)
        convergence = run_mesh_convergence(inputs) if convergence_requested else {}
        gmsh, sfepy = _require_fea_dependencies()
        import meshio

        package = Stage1FeaPackage(
            schema_version=SCHEMA_VERSION,
            shaft=shaft,
            gear=gear,
            drum=drum,
            solver_versions={
                "gmsh": str(gmsh.__version__),
                "sfepy": str(sfepy.__version__),
                "meshio": str(meshio.__version__),
                "numpy": str(np.__version__),
            },
            assumptions={
                "analysis": "Linear-static FEA with reduced-order water loading; not CFD.",
                "request_identity": canonical_request_identity(inputs, levels),
                "load_model_limitations": (
                    "Reduced-order side pressure and retained-water back traction; not CFD or fluid-structure interaction"
                ),
                "convergence_policy": (
                    "Medium/fine worst-phase unconstrained P95 stress and maximum displacement must each change by less than 10%; otherwise sensitivity is unresolved"
                ),
                "safety_factor_policy": (
                    f"Yield strength divided by nodal von Mises stress; zero-stress values capped at {FACTOR_OF_SAFETY_CAP:.0e}"
                ),
                "symmetric_tensor_order": ",".join(SYMMETRIC_TENSOR_COMPONENTS),
                "drum_wall_model": (
                    "Equivalent hollow solid with two forced radial layers and no explicit perforations"
                ),
                "drum_gravity_load": (
                    "Retained-water weight is distributed over the gravity-wetted internal back region"
                ),
                "drum_solid_tet_limitation": (
                    "Two forced radial layers improve thickness sampling but do not imply shell convergence"
                ),
                "gear_model": "Toothless disc and hub with a cylindrical shaft bore constraint",
                "shaft_load_station_m": inputs.chain_force_overhang_m,
                "shaft_reaction_load_n": inputs.shaft_transverse_reaction_n,
                "shaft_reaction_station_m": inputs.shaft_reaction_overhang_m,
                "enclosure_scope": "Enclosure dimensions are reported inputs and are not solved",
                "mesh_level": selected_level,
                "units": "SI: m, N, Pa, degrees",
            },
            convergence=convergence,
            inputs=inputs.to_dict(),
            analytical_values=asdict(analytical),
        )
        save_stage1_package(package, args.output)
        _progress(1.00, "package saved")
        return 0
    except Exception as error:
        print(f"ERROR {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
