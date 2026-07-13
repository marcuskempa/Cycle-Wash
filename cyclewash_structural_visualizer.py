"""Lightweight STL assembly loading and stress visualization helpers.

The stress fields here are geometric teaching approximations, not finite
element analysis. They are intended to make structural load paths visible in
the Streamlit/Plotly CycleWash dashboard without adding a heavy simulation
dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

import numpy as np


ArrayLike3 = tuple[float, float, float] | list[float] | np.ndarray


class StlParseError(ValueError):
    """Raised when STL geometry cannot be parsed."""


@dataclass(frozen=True)
class Transform:
    """Coordinate transform for an assembly part."""

    translation: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=float))
    rotation_matrix: np.ndarray = field(default_factory=lambda: np.eye(3, dtype=float))
    rotation_origin: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=float))

    @classmethod
    def from_translation(cls, translation: ArrayLike3 | None = None) -> "Transform":
        """Build a transform from a 3D translation vector."""
        if translation is None:
            return cls()
        vector = np.asarray(translation, dtype=float)
        if vector.shape != (3,):
            raise ValueError("translation must contain exactly 3 values")
        return cls(translation=vector)

    @classmethod
    def from_rotation(
        cls,
        axis_vector: ArrayLike3,
        angle_degrees: float,
        origin: ArrayLike3 | None = None,
        translation: ArrayLike3 | None = None,
    ) -> "Transform":
        """Build a transform with rotation about an arbitrary axis."""
        origin_vector = np.zeros(3, dtype=float) if origin is None else np.asarray(origin, dtype=float)
        if origin_vector.shape != (3,):
            raise ValueError("origin must contain exactly 3 values")

        translation_vector = (
            np.zeros(3, dtype=float) if translation is None else np.asarray(translation, dtype=float)
        )
        if translation_vector.shape != (3,):
            raise ValueError("translation must contain exactly 3 values")

        return cls(
            translation=translation_vector,
            rotation_matrix=rotation_matrix_from_axis_angle(axis_vector, angle_degrees),
            rotation_origin=origin_vector,
        )

    def apply(self, vertices: np.ndarray) -> np.ndarray:
        """Apply this transform to an array of vertices."""
        centered_vertices = vertices - self.rotation_origin
        rotated_vertices = centered_vertices @ self.rotation_matrix.T
        return rotated_vertices + self.rotation_origin + self.translation


@dataclass(frozen=True)
class AssemblyPart:
    """One independently transformable component in a 3D assembly."""

    name: str
    local_vertices: np.ndarray
    faces: np.ndarray
    transform: Transform = field(default_factory=Transform)
    material_color: str = "#9ca3af"
    component_kind: str = "casing"

    @property
    def vertices(self) -> np.ndarray:
        """World-space vertices after applying the part transform."""
        return self.transform.apply(self.local_vertices)

    @property
    def face_vertices(self) -> np.ndarray:
        """World-space triangle vertices with shape `(face_count, 3, 3)`."""
        return self.vertices[self.faces]

    @property
    def face_centers(self) -> np.ndarray:
        """World-space triangle centroids."""
        return self.face_vertices.mean(axis=1)

    @property
    def local_face_centers(self) -> np.ndarray:
        """Local-space triangle centroids."""
        return self.local_vertices[self.faces].mean(axis=1)

    @property
    def triangle_count(self) -> int:
        """Number of triangular facets in the part."""
        return int(self.faces.shape[0])

    @property
    def vertex_count(self) -> int:
        """Number of unique vertices in the part."""
        return int(self.local_vertices.shape[0])

    def translated(self, translation: ArrayLike3) -> "AssemblyPart":
        """Return a copy of this part with a new translation vector."""
        return AssemblyPart(
            name=self.name,
            local_vertices=self.local_vertices,
            faces=self.faces,
            transform=Transform.from_translation(translation),
            material_color=self.material_color,
            component_kind=self.component_kind,
        )

    def transformed(self, transform: Transform) -> "AssemblyPart":
        """Return a copy of this part with a new transform."""
        return AssemblyPart(
            name=self.name,
            local_vertices=self.local_vertices,
            faces=self.faces,
            transform=transform,
            material_color=self.material_color,
            component_kind=self.component_kind,
        )


@dataclass
class Assembly:
    """Collection of independently transformable assembly parts."""

    parts: list[AssemblyPart] = field(default_factory=list)

    def add_part(self, part: AssemblyPart) -> None:
        """Append a part to the assembly."""
        self.parts.append(part)

    def get_part(self, name: str) -> AssemblyPart:
        """Return a part by name."""
        for part in self.parts:
            if part.name == name:
                return part
        raise KeyError(f"assembly part not found: {name}")

    @classmethod
    def from_stl_specs(cls, specs: list["StlPartSpec"]) -> "Assembly":
        """Load an assembly from a list of STL part specifications."""
        assembly = cls()
        for spec in specs:
            assembly.add_part(load_stl_part(spec))
        return assembly


@dataclass(frozen=True)
class StlPartSpec:
    """Input description for one STL component."""

    name: str
    source: str | Path | bytes | BinaryIO
    translation: ArrayLike3 = (0.0, 0.0, 0.0)
    material_color: str = "#9ca3af"
    component_kind: str = "casing"


def load_stl_part(spec: StlPartSpec) -> AssemblyPart:
    """Load one STL file or byte stream into an `AssemblyPart`."""
    raw_bytes = _read_stl_bytes(spec.source)
    triangle_vertices = _parse_binary_stl(raw_bytes)
    if triangle_vertices is None:
        triangle_vertices = _parse_ascii_stl(raw_bytes)

    local_vertices, faces = _deduplicate_triangle_vertices(triangle_vertices)
    return AssemblyPart(
        name=spec.name,
        local_vertices=local_vertices,
        faces=faces,
        transform=Transform.from_translation(spec.translation),
        material_color=spec.material_color,
        component_kind=spec.component_kind,
    )


def _read_stl_bytes(source: str | Path | bytes | BinaryIO) -> bytes:
    """Read STL bytes from a path, raw bytes, or a binary file-like object."""
    if isinstance(source, bytes):
        return source
    if isinstance(source, str | Path):
        return Path(source).read_bytes()
    data = source.read()
    if isinstance(data, str):
        return data.encode("utf-8")
    return data


def _parse_binary_stl(raw_bytes: bytes) -> np.ndarray | None:
    """Parse binary STL bytes, returning triangle vertices or `None`."""
    if len(raw_bytes) < 84:
        return None

    triangle_count = int(np.frombuffer(raw_bytes[80:84], dtype="<u4", count=1)[0])
    expected_length = 84 + triangle_count * 50
    if len(raw_bytes) != expected_length:
        return None

    record_dtype = np.dtype(
        [
            ("normal", "<f4", (3,)),
            ("vertices", "<f4", (3, 3)),
            ("attribute", "<u2"),
        ]
    )
    records = np.frombuffer(raw_bytes, dtype=record_dtype, count=triangle_count, offset=84)
    return np.asarray(records["vertices"], dtype=float)


def _parse_ascii_stl(raw_bytes: bytes) -> np.ndarray:
    """Parse ASCII STL bytes and return triangle vertices."""
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise StlParseError("STL is neither valid binary nor UTF-8 ASCII") from error

    vertices: list[list[float]] = []
    for line in text.splitlines():
        words = line.strip().split()
        if len(words) == 4 and words[0].lower() == "vertex":
            vertices.append([float(words[1]), float(words[2]), float(words[3])])

    if len(vertices) == 0 or len(vertices) % 3 != 0:
        raise StlParseError("ASCII STL must contain vertex records in groups of 3")

    return np.asarray(vertices, dtype=float).reshape((-1, 3, 3))


def _deduplicate_triangle_vertices(
    triangle_vertices: np.ndarray,
    decimals: int = 6,
) -> tuple[np.ndarray, np.ndarray]:
    """Convert repeated STL triangle vertices into unique nodes and faces."""
    flat_vertices = np.asarray(triangle_vertices, dtype=float).reshape((-1, 3))
    rounded_vertices = np.round(flat_vertices, decimals=decimals)
    _, unique_indices, inverse_indices = np.unique(
        rounded_vertices,
        axis=0,
        return_index=True,
        return_inverse=True,
    )
    unique_vertices = flat_vertices[unique_indices]
    faces = inverse_indices.reshape((-1, 3)).astype(int)
    return unique_vertices, faces


def normalize_values(values: np.ndarray) -> np.ndarray:
    """Normalize numeric values into the range 0.0 to 1.0."""
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return values
    value_min = float(values.min())
    value_max = float(values.max())
    if np.isclose(value_min, value_max):
        return np.zeros_like(values, dtype=float)
    return (values - value_min) / (value_max - value_min)


def rotation_matrix_from_axis_angle(axis_vector: ArrayLike3, angle_degrees: float) -> np.ndarray:
    """Return a 3x3 rotation matrix using Rodrigues' rotation formula."""
    axis = _unit_vector(axis_vector, "axis_vector")
    angle_radians = np.deg2rad(angle_degrees)
    axis_x, axis_y, axis_z = axis
    cos_angle = np.cos(angle_radians)
    sin_angle = np.sin(angle_radians)
    one_minus_cos = 1.0 - cos_angle
    return np.asarray(
        [
            [
                cos_angle + axis_x * axis_x * one_minus_cos,
                axis_x * axis_y * one_minus_cos - axis_z * sin_angle,
                axis_x * axis_z * one_minus_cos + axis_y * sin_angle,
            ],
            [
                axis_y * axis_x * one_minus_cos + axis_z * sin_angle,
                cos_angle + axis_y * axis_y * one_minus_cos,
                axis_y * axis_z * one_minus_cos - axis_x * sin_angle,
            ],
            [
                axis_z * axis_x * one_minus_cos - axis_y * sin_angle,
                axis_z * axis_y * one_minus_cos + axis_x * sin_angle,
                cos_angle + axis_z * axis_z * one_minus_cos,
            ],
        ],
        dtype=float,
    )


def axis_gradient_stress(
    part: AssemblyPart,
    axis_vector: ArrayLike3 = (0.0, 0.0, -1.0),
    per_face: bool = False,
) -> np.ndarray:
    """Compute a normalized stress field along a world-space axis vector.

    With the default `(0, 0, -1)`, lower-Z geometry receives higher stress,
    which is useful for simulating water/gravity load on a casing.
    """
    axis = _unit_vector(axis_vector, "axis_vector")
    points = part.face_centers if per_face else part.vertices
    projected = points @ axis
    return normalize_values(projected)


def radial_torsion_stress(
    part: AssemblyPart,
    axis_vector: ArrayLike3 = (0.0, 0.0, 1.0),
    axis_origin: ArrayLike3 | None = None,
    per_face: bool = False,
) -> np.ndarray:
    """Compute normalized torsional shear stress from radius about an axis."""
    axis = _unit_vector(axis_vector, "axis_vector")
    points = part.face_centers if per_face else part.vertices
    if axis_origin is None:
        origin = points.mean(axis=0)
    else:
        origin = np.asarray(axis_origin, dtype=float)
        if origin.shape != (3,):
            raise ValueError("axis_origin must contain exactly 3 values")

    relative_points = points - origin
    axial_distance = relative_points @ axis
    axial_projection = np.outer(axial_distance, axis)
    radial_vectors = relative_points - axial_projection
    radial_distances = np.linalg.norm(radial_vectors, axis=1)
    return normalize_values(radial_distances)


def rotating_water_stress(
    part: AssemblyPart,
    rotation_axis: ArrayLike3 = (0.0, 1.0, 0.0),
    gravity_axis: ArrayLike3 = (0.0, 0.0, -1.0),
    axis_origin: ArrayLike3 | None = None,
    rpm: float = 60.0,
    rotation_angle_degrees: float = 0.0,
    water_fill_fraction: float = 0.35,
    perforation_relief: float = 0.45,
    per_face: bool = False,
) -> np.ndarray:
    """Approximate spin, water, and perforation-driven stress on a rotating part.

    The result combines radial centrifugal/torsional loading with a rotating
    slosh band and a gravity-biased water head. `perforation_relief` represents
    how much through-hole circulation reduces retained water pressure.
    """
    if rpm < 0.0:
        raise ValueError("rpm must be non-negative")
    if not 0.0 <= water_fill_fraction <= 1.0:
        raise ValueError("water_fill_fraction must be between 0.0 and 1.0")
    if not 0.0 <= perforation_relief <= 1.0:
        raise ValueError("perforation_relief must be between 0.0 and 1.0")

    axis = _unit_vector(rotation_axis, "rotation_axis")
    gravity = _unit_vector(gravity_axis, "gravity_axis")
    points = part.face_centers if per_face else part.vertices
    origin = np.zeros(3, dtype=float) if axis_origin is None else np.asarray(axis_origin, dtype=float)
    if origin.shape != (3,):
        raise ValueError("axis_origin must contain exactly 3 values")

    relative_points = points - origin
    axial_distance = relative_points @ axis
    radial_vectors = relative_points - np.outer(axial_distance, axis)
    radial_distance = np.linalg.norm(radial_vectors, axis=1)
    radial_term = normalize_values(radial_distance**2)

    basis_a = _perpendicular_unit_vector(axis)
    basis_b = np.cross(axis, basis_a)
    circumferential_angle = np.arctan2(radial_vectors @ basis_b, radial_vectors @ basis_a)
    phase = np.deg2rad(rotation_angle_degrees)
    circulation_term = 0.5 + 0.5 * np.sin(circumferential_angle - phase)

    gravity_head = normalize_values(points @ gravity)
    rpm_scale = min((rpm / 120.0) ** 2, 4.0)
    retained_water = water_fill_fraction * (1.0 - perforation_relief)
    combined = (
        0.55 * rpm_scale * radial_term
        + retained_water * (0.30 * gravity_head + 0.15 * circulation_term)
    )
    return normalize_values(combined)


def stationary_water_stress(
    part: AssemblyPart,
    gravity_axis: ArrayLike3 = (0.0, 0.0, -1.0),
    water_fill_fraction: float = 0.35,
    perforation_relief: float = 0.45,
    per_face: bool = False,
) -> np.ndarray:
    """Approximate hydrostatic drum load without rotation or circulation phase."""
    if not 0.0 <= water_fill_fraction <= 1.0:
        raise ValueError("water_fill_fraction must be between 0.0 and 1.0")
    if not 0.0 <= perforation_relief <= 1.0:
        raise ValueError("perforation_relief must be between 0.0 and 1.0")

    gravity = _unit_vector(gravity_axis, "gravity_axis")
    points = part.face_centers if per_face else part.vertices
    normalized_height = normalize_values(points @ gravity)
    free_surface = 1.0 - water_fill_fraction
    water_depth = np.clip(
        normalized_height - free_surface,
        0.0,
        water_fill_fraction,
    )
    return (1.0 - perforation_relief) * water_depth


def hinge_lever_stress(
    part: AssemblyPart,
    hinge_axis: ArrayLike3 = (0.0, 0.0, 1.0),
    hinge_origin: ArrayLike3 = (0.0, 0.0, 0.0),
    per_face: bool = False,
) -> np.ndarray:
    """Approximate door hinge bending stress from distance to a hinge axis."""
    axis = _unit_vector(hinge_axis, "hinge_axis")
    origin = np.asarray(hinge_origin, dtype=float)
    if origin.shape != (3,):
        raise ValueError("hinge_origin must contain exactly 3 values")
    points = part.face_centers if per_face else part.vertices
    relative_points = points - origin
    axial_distance = relative_points @ axis
    radial_vectors = relative_points - np.outer(axial_distance, axis)
    return normalize_values(np.linalg.norm(radial_vectors, axis=1))


def _unit_vector(vector: ArrayLike3, name: str) -> np.ndarray:
    """Return a normalized 3D vector."""
    array = np.asarray(vector, dtype=float)
    if array.shape != (3,):
        raise ValueError(f"{name} must contain exactly 3 values")
    magnitude = float(np.linalg.norm(array))
    if np.isclose(magnitude, 0.0):
        raise ValueError(f"{name} must not be the zero vector")
    return array / magnitude


def _perpendicular_unit_vector(axis: np.ndarray) -> np.ndarray:
    """Return any stable unit vector perpendicular to `axis`."""
    candidate = np.asarray([1.0, 0.0, 0.0], dtype=float)
    if abs(float(np.dot(candidate, axis))) > 0.9:
        candidate = np.asarray([0.0, 0.0, 1.0], dtype=float)
    perpendicular = candidate - np.dot(candidate, axis) * axis
    return perpendicular / np.linalg.norm(perpendicular)


def mesh3d_kwargs(
    part: AssemblyPart,
    stress_values: np.ndarray | None = None,
    color_mode: str = "stress",
    colorscale: str = "Turbo",
    opacity: float = 0.82,
    show_scale: bool = True,
) -> dict[str, object]:
    """Package a part as Plotly `go.Mesh3d` keyword arguments."""
    vertices = part.vertices
    faces = part.faces
    kwargs: dict[str, object] = {
        "x": vertices[:, 0],
        "y": vertices[:, 1],
        "z": vertices[:, 2],
        "i": faces[:, 0],
        "j": faces[:, 1],
        "k": faces[:, 2],
        "name": part.name,
        "opacity": opacity,
        "flatshading": True,
        "hovertemplate": (
            f"{part.name}<br>"
            "x: %{x:.2f}<br>"
            "y: %{y:.2f}<br>"
            "z: %{z:.2f}<extra></extra>"
        ),
    }

    if color_mode.lower().startswith("stress") and stress_values is not None:
        stress_values = np.asarray(stress_values, dtype=float)
        kwargs.update(
            {
                "intensity": stress_values,
                "intensitymode": "cell" if stress_values.shape[0] == faces.shape[0] else "vertex",
                "colorscale": colorscale,
                "cmin": 0.0,
                "cmax": 1.0,
                "showscale": show_scale,
                "colorbar": {"title": "Stress"},
            }
        )
    else:
        kwargs.update({"color": part.material_color, "showscale": False})
    return kwargs


def calculate_part_stress(
    part: AssemblyPart,
    load_case: str,
    casing_axis: ArrayLike3 = (0.0, 0.0, -1.0),
    rotation_axis: ArrayLike3 = (0.0, 0.0, 1.0),
    per_face: bool = False,
) -> np.ndarray:
    """Calculate a stress field for one part from a named load case."""
    normalized_case = load_case.lower()
    if "radial" in normalized_case or "torsion" in normalized_case:
        return radial_torsion_stress(part, axis_vector=rotation_axis, per_face=per_face)
    if "axis" in normalized_case or "casing" in normalized_case or "gravity" in normalized_case:
        return axis_gradient_stress(part, axis_vector=casing_axis, per_face=per_face)
    if part.component_kind.lower() in {"shaft", "gear", "rotational"}:
        return radial_torsion_stress(part, axis_vector=rotation_axis, per_face=per_face)
    return axis_gradient_stress(part, axis_vector=casing_axis, per_face=per_face)
