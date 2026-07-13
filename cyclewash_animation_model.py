"""Procedural water geometry and deterministic animation metadata for CycleWash."""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral, Real
from typing import Final

import numpy as np
from numpy.typing import NDArray


FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
SLOSH_LIMIT_DEG: Final[float] = 8.0
SLOSH_LAG_DEG: Final[float] = 45.0
_EPSILON: Final[float] = 1e-12


@dataclass(frozen=True)
class WaterMesh:
    """A closed, outward-wound water volume and the vertices of its free surface."""

    vertices: FloatArray
    triangles: IntArray
    surface_vertex_indices: IntArray

    def __post_init__(self) -> None:
        vertices = np.array(self.vertices, dtype=np.float64, copy=True)
        triangles = np.array(self.triangles, dtype=np.int64, copy=True)
        surface_indices = np.array(self.surface_vertex_indices, dtype=np.int64, copy=True)
        if vertices.ndim != 2 or vertices.shape[1:] != (3,):
            raise ValueError("vertices must have shape (n, 3)")
        if triangles.ndim != 2 or triangles.shape[1:] != (3,):
            raise ValueError("triangles must have shape (n, 3)")
        if surface_indices.ndim != 1:
            raise ValueError("surface_vertex_indices must be one-dimensional")
        if not np.isfinite(vertices).all():
            raise ValueError("vertices must be finite")
        if triangles.size and (triangles.min() < 0 or triangles.max() >= len(vertices)):
            raise ValueError("triangles contain an invalid vertex index")
        if surface_indices.size and (
            surface_indices.min() < 0 or surface_indices.max() >= len(vertices)
        ):
            raise ValueError("surface_vertex_indices contain an invalid vertex index")
        if len(np.unique(surface_indices)) != len(surface_indices):
            raise ValueError("surface_vertex_indices must be unique")
        vertices.setflags(write=False)
        triangles.setflags(write=False)
        surface_indices.setflags(write=False)
        object.__setattr__(self, "vertices", vertices)
        object.__setattr__(self, "triangles", triangles)
        object.__setattr__(self, "surface_vertex_indices", surface_indices)


@dataclass(frozen=True)
class AnimationSample:
    """Scalar animation state; it deliberately never carries geometry."""

    time_s: float
    phase_deg: float
    slosh_angle_deg: float
    pressure_scale_pa: float

    def __post_init__(self) -> None:
        values = (self.time_s, self.phase_deg, self.slosh_angle_deg, self.pressure_scale_pa)
        if not all(np.isfinite(value) for value in values):
            raise ValueError("animation sample values must be finite")
        if self.time_s < 0.0:
            raise ValueError("time_s must be non-negative")
        if abs(self.slosh_angle_deg) > SLOSH_LIMIT_DEG + _EPSILON:
            raise ValueError(f"slosh_angle_deg must not exceed {SLOSH_LIMIT_DEG} degrees")
        if self.pressure_scale_pa < 0.0:
            raise ValueError("pressure_scale_pa must be non-negative")


def build_partial_cylinder_water_mesh(
    axis_origin: object,
    axis: object,
    gravity_axis: object,
    radius: float,
    length: float,
    fill_fraction: float,
    segments: int = 48,
) -> WaterMesh:
    """Build the intersection of a capped cylinder and a gravity-level half-space.

    ``fill_fraction`` is an actual volume fraction, including when the shaft is
    inclined relative to gravity.  A circle-segment area integral is inverted by
    bisection to find the free-surface height, then a triangulated cylinder is
    clipped against that plane and capped with a flat surface.
    """

    origin = _finite_vector(axis_origin, "axis_origin")
    shaft_axis = _unit_vector(axis, "axis")
    downward = _unit_vector(gravity_axis, "gravity_axis")
    radius_value = _positive_finite(radius, "radius")
    length_value = _positive_finite(length, "length")
    fraction = _bounded_fraction(fill_fraction)
    segment_count = _segment_count(segments)

    upward = -downward
    transverse_up = upward - np.dot(upward, shaft_axis) * shaft_axis
    transverse_up_norm = float(np.linalg.norm(transverse_up))
    if transverse_up_norm <= _EPSILON:
        raise ValueError("axis and gravity_axis must not be parallel")
    cross_up = transverse_up / transverse_up_norm
    cross_side = np.cross(cross_up, shaft_axis)
    cross_side /= np.linalg.norm(cross_side)

    if fraction == 0.0:
        return WaterMesh(
            vertices=np.empty((0, 3), dtype=np.float64),
            triangles=np.empty((0, 3), dtype=np.int64),
            surface_vertex_indices=np.empty(0, dtype=np.int64),
        )

    full_vertices, full_triangles = _build_capped_cylinder(
        origin, shaft_axis, cross_side, cross_up, radius_value, length_value, segment_count
    )
    if fraction == 1.0:
        return WaterMesh(
            vertices=full_vertices,
            triangles=full_triangles,
            surface_vertex_indices=np.empty(0, dtype=np.int64),
        )

    waterline = _waterline_height(
        fraction, radius_value, length_value, float(np.dot(upward, shaft_axis)), transverse_up_norm
    )
    vertices, triangles, surface_indices = _clip_cylinder_below_waterline(
        full_vertices, full_triangles, origin, upward, waterline
    )
    return WaterMesh(vertices=vertices, triangles=triangles, surface_vertex_indices=surface_indices)


def build_animation_timeline(
    rpm: float,
    duration_s: float,
    sample_count: int,
    slosh_amplitude_deg: float,
    pressure_scale_pa: float,
) -> list[AnimationSample]:
    """Return deterministic scalar timeline samples without duplicating geometry."""

    rpm_value = _finite_real(rpm, "rpm")
    duration_value = _finite_real(duration_s, "duration_s")
    pressure_value = _finite_real(pressure_scale_pa, "pressure_scale_pa")
    requested_amplitude = _finite_real(slosh_amplitude_deg, "slosh_amplitude_deg")
    if duration_value < 0.0:
        raise ValueError("duration_s must be non-negative")
    if pressure_value < 0.0:
        raise ValueError("pressure_scale_pa must be non-negative")
    if requested_amplitude < 0.0:
        raise ValueError("slosh_amplitude_deg must be non-negative")
    if not isinstance(sample_count, Integral) or isinstance(sample_count, bool) or sample_count < 1:
        raise ValueError("sample_count must be an integer of at least one")

    amplitude = min(float(requested_amplitude), SLOSH_LIMIT_DEG)
    times = np.linspace(0.0, duration_value, int(sample_count), dtype=np.float64)
    phase_degrees = 360.0 * (rpm_value / 60.0) * times
    slosh_degrees = amplitude * np.sin(np.deg2rad(phase_degrees - SLOSH_LAG_DEG))
    return [
        AnimationSample(
            time_s=float(time_s),
            phase_deg=float(phase_deg),
            slosh_angle_deg=float(slosh_deg),
            pressure_scale_pa=pressure_value,
        )
        for time_s, phase_deg, slosh_deg in zip(times, phase_degrees, slosh_degrees, strict=True)
    ]


def mesh_volume(mesh: WaterMesh) -> float:
    """Return the positive volume enclosed by a consistently-wound triangle mesh."""

    if not isinstance(mesh, WaterMesh):
        raise TypeError("mesh must be a WaterMesh")
    if mesh.triangles.size == 0:
        return 0.0
    triangles = mesh.vertices[mesh.triangles]
    signed_volume = np.einsum(
        "ij,ij->i", triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])
    ).sum() / 6.0
    return float(abs(signed_volume))


def _build_capped_cylinder(
    origin: FloatArray,
    shaft_axis: FloatArray,
    cross_side: FloatArray,
    cross_up: FloatArray,
    radius: float,
    length: float,
    segments: int,
) -> tuple[FloatArray, IntArray]:
    angles = 2.0 * np.pi * np.arange(segments, dtype=np.float64) / segments
    ring_directions = (
        np.cos(angles)[:, None] * cross_side[None, :]
        + np.sin(angles)[:, None] * cross_up[None, :]
    )
    start_ring = origin[None, :] + radius * ring_directions
    end_center = origin + length * shaft_axis
    end_ring = end_center[None, :] + radius * ring_directions
    vertices = np.vstack((start_ring, end_ring, origin[None, :], end_center[None, :]))
    start_center_index = 2 * segments
    end_center_index = start_center_index + 1
    triangles: list[tuple[int, int, int]] = []
    for index in range(segments):
        next_index = (index + 1) % segments
        start = index
        start_next = next_index
        end = segments + index
        end_next = segments + next_index
        triangles.extend(
            (
                (start, start_next, end),
                (start_next, end_next, end),
                (start_center_index, start_next, start),
                (end_center_index, end, end_next),
            )
        )
    return vertices, np.asarray(triangles, dtype=np.int64)


def _waterline_height(
    fill_fraction: float,
    radius: float,
    length: float,
    axis_up_component: float,
    transverse_up_magnitude: float,
) -> float:
    """Invert the integrated circular-segment volume by monotonic bisection."""

    quadrature_nodes, quadrature_weights = np.polynomial.legendre.leggauss(48)
    axial_positions = (quadrature_nodes + 1.0) * (length / 2.0)
    axial_weights = quadrature_weights * (length / 2.0)
    minimum = min(0.0, axis_up_component * length) - transverse_up_magnitude * radius
    maximum = max(0.0, axis_up_component * length) + transverse_up_magnitude * radius
    target_volume = fill_fraction * np.pi * radius**2 * length

    def volume_below(height: float) -> float:
        chord_heights = (height - axis_up_component * axial_positions) / transverse_up_magnitude
        areas = _circle_area_below_chord(chord_heights, radius)
        return float(np.dot(axial_weights, areas))

    lower, upper = minimum, maximum
    for _ in range(80):
        midpoint = (lower + upper) / 2.0
        if volume_below(midpoint) < target_volume:
            lower = midpoint
        else:
            upper = midpoint
    return (lower + upper) / 2.0


def _circle_area_below_chord(chord_height: NDArray[np.float64], radius: float) -> NDArray[np.float64]:
    normalized = np.clip(chord_height / radius, -1.0, 1.0)
    root = np.sqrt(np.maximum(0.0, 1.0 - normalized**2))
    return radius**2 * (np.arcsin(normalized) + (np.pi / 2.0) + normalized * root)


def _clip_cylinder_below_waterline(
    vertices: FloatArray,
    triangles: IntArray,
    origin: FloatArray,
    upward: FloatArray,
    waterline: float,
) -> tuple[FloatArray, IntArray, IntArray]:
    """Clip outward cylinder triangles and add an upward-wound planar cap."""

    output_vertices: list[FloatArray] = []
    vertex_ids: dict[tuple[float, float, float], int] = {}
    output_triangles: list[tuple[int, int, int]] = []
    cut_segments: set[tuple[int, int]] = set()

    def add_vertex(point: FloatArray) -> int:
        key = tuple(np.round(point, decimals=12))
        index = vertex_ids.get(key)
        if index is None:
            index = len(output_vertices)
            vertex_ids[key] = index
            output_vertices.append(np.array(point, dtype=np.float64, copy=True))
        return index

    for triangle in triangles:
        polygon = [vertices[index] for index in triangle]
        distances = [float(np.dot(upward, point - origin) - waterline) for point in polygon]
        clipped_points: list[FloatArray] = []
        previous_point = polygon[-1]
        previous_distance = distances[-1]
        for point, distance in zip(polygon, distances, strict=True):
            previous_inside = previous_distance <= _EPSILON
            inside = distance <= _EPSILON
            if inside != previous_inside:
                interpolation = previous_distance / (previous_distance - distance)
                clipped_points.append(previous_point + interpolation * (point - previous_point))
            if inside:
                clipped_points.append(point)
            previous_point, previous_distance = point, distance

        if len(clipped_points) < 3:
            continue
        clipped_ids = [add_vertex(point) for point in clipped_points]
        plane_ids = [
            index
            for point, index in zip(clipped_points, clipped_ids, strict=True)
            if abs(float(np.dot(upward, point - origin) - waterline)) <= 1e-9
        ]
        if len(plane_ids) >= 2:
            unique_plane_ids = list(dict.fromkeys(plane_ids))
            if len(unique_plane_ids) == 2:
                cut_segments.add(tuple(sorted(unique_plane_ids)))
        for index in range(1, len(clipped_ids) - 1):
            candidate = (clipped_ids[0], clipped_ids[index], clipped_ids[index + 1])
            if _triangle_has_area(output_vertices, candidate):
                output_triangles.append(candidate)

    if not cut_segments:
        raise RuntimeError("partial-fill clipping did not produce a free surface")
    surface_loop = _ordered_surface_loop(output_vertices, cut_segments, upward)
    cap_center = np.mean(np.asarray([output_vertices[index] for index in surface_loop]), axis=0)
    cap_center_id = add_vertex(cap_center)
    for index, next_index in zip(surface_loop, surface_loop[1:] + surface_loop[:1], strict=True):
        candidate = (cap_center_id, index, next_index)
        if not _triangle_has_area(output_vertices, candidate):
            continue
        normal = np.cross(
            output_vertices[index] - output_vertices[cap_center_id],
            output_vertices[next_index] - output_vertices[cap_center_id],
        )
        if np.dot(normal, upward) < 0.0:
            candidate = (cap_center_id, next_index, index)
        output_triangles.append(candidate)

    return (
        np.asarray(output_vertices, dtype=np.float64),
        np.asarray(output_triangles, dtype=np.int64),
        np.asarray(sorted(set(surface_loop + [cap_center_id])), dtype=np.int64),
    )


def _ordered_surface_loop(
    vertices: list[FloatArray], cut_segments: set[tuple[int, int]], upward: FloatArray
) -> list[int]:
    adjacency: dict[int, list[int]] = {}
    for first, second in cut_segments:
        adjacency.setdefault(first, []).append(second)
        adjacency.setdefault(second, []).append(first)
    if not adjacency or any(len(neighbors) != 2 for neighbors in adjacency.values()):
        raise RuntimeError("free-surface boundary is not a single closed loop")

    loop = [next(iter(adjacency))]
    previous: int | None = None
    while True:
        current = loop[-1]
        candidates = [neighbor for neighbor in adjacency[current] if neighbor != previous]
        next_index = candidates[0]
        if next_index == loop[0]:
            break
        if next_index in loop:
            raise RuntimeError("free-surface boundary has an unexpected cycle")
        loop.append(next_index)
        previous = current
    if len(loop) != len(adjacency):
        raise RuntimeError("free-surface boundary contains disjoint loops")

    center = np.mean(np.asarray([vertices[index] for index in loop]), axis=0)
    basis_first = _perpendicular_unit(upward)
    basis_second = np.cross(upward, basis_first)
    return sorted(
        loop,
        key=lambda index: float(
            np.arctan2(
                np.dot(vertices[index] - center, basis_second),
                np.dot(vertices[index] - center, basis_first),
            )
        ),
    )


def _triangle_has_area(vertices: list[FloatArray], triangle: tuple[int, int, int]) -> bool:
    first, second, third = (vertices[index] for index in triangle)
    return float(np.linalg.norm(np.cross(second - first, third - first))) > _EPSILON


def _perpendicular_unit(vector: FloatArray) -> FloatArray:
    reference = np.array([1.0, 0.0, 0.0])
    if abs(float(np.dot(vector, reference))) > 0.9:
        reference = np.array([0.0, 1.0, 0.0])
    perpendicular = np.cross(vector, reference)
    return perpendicular / np.linalg.norm(perpendicular)


def _finite_vector(value: object, name: str) -> FloatArray:
    array = np.asarray(value, dtype=np.float64)
    if array.shape != (3,) or not np.isfinite(array).all():
        raise ValueError(f"{name} must be a finite three-dimensional vector")
    return np.array(array, dtype=np.float64, copy=True)


def _unit_vector(value: object, name: str) -> FloatArray:
    vector = _finite_vector(value, name)
    magnitude = float(np.linalg.norm(vector))
    if magnitude <= _EPSILON:
        raise ValueError(f"{name} must have non-zero magnitude")
    return vector / magnitude


def _finite_real(value: object, name: str) -> float:
    if not isinstance(value, Real) or isinstance(value, bool) or not np.isfinite(value):
        raise ValueError(f"{name} must be a finite real number")
    return float(value)


def _positive_finite(value: object, name: str) -> float:
    result = _finite_real(value, name)
    if result <= 0.0:
        raise ValueError(f"{name} must be greater than zero")
    return result


def _bounded_fraction(value: object) -> float:
    result = _finite_real(value, "fill_fraction")
    if not 0.0 <= result <= 1.0:
        raise ValueError("fill_fraction must be between zero and one")
    return result


def _segment_count(value: object) -> int:
    if not isinstance(value, Integral) or isinstance(value, bool) or value < 3:
        raise ValueError("segments must be an integer of at least three")
    return int(value)
