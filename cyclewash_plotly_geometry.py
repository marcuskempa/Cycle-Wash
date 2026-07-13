"""Reusable Plotly mesh geometry helpers for the CycleWash rear cog."""

from __future__ import annotations

import importlib.util
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


def _load_calculator_module():
    """Load the sibling calculator module when outputs/ is not on sys.path."""
    try:
        import cyclewash_cad_calculator as calculator

        return calculator
    except ModuleNotFoundError:
        module_path = Path(__file__).resolve().parent / "cyclewash_cad_calculator.py"
        spec = importlib.util.spec_from_file_location("cyclewash_cad_calculator", module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module


calculator = _load_calculator_module()
calculate_pitch_radius_mm = calculator.calculate_pitch_radius_mm
calculate_sprocket_radii_mm = calculator.calculate_sprocket_radii_mm
make_sprocket_profile_points = calculator.make_sprocket_profile_points
validate_positive = calculator.validate_positive


@dataclass(frozen=True)
class GearMeshData:
    """Triangle mesh data ready for plotly.graph_objects.Mesh3d."""

    x: list[float]
    y: list[float]
    z: list[float]
    i: list[int]
    j: list[int]
    k: list[int]
    pitch_radius_mm: float
    root_radius_mm: float
    outer_radius_mm: float
    bore_radius_mm: float
    segment_count: int


def calculate_simplified_cog_radii(
    tooth_count: int,
    chain_pitch_mm: float,
    shaft_radius_mm: float,
) -> tuple[float, float, float, float]:
    """Return pitch, root, outer, and bore radii for a simplified rear cog."""
    return calculate_sprocket_radii_mm(tooth_count, chain_pitch_mm, shaft_radius_mm)


def build_gear_mesh_data(
    tooth_count: int,
    chain_pitch_mm: float,
    shaft_radius_mm: float,
    cog_thickness_mm: float,
) -> GearMeshData:
    """Build a holed, extruded, simplified sprocket mesh for Plotly."""
    validate_positive("cog_thickness_mm", cog_thickness_mm)
    pitch_radius_mm, root_radius_mm, outer_radius_mm, bore_radius_mm = calculate_simplified_cog_radii(
        tooth_count=tooth_count,
        chain_pitch_mm=chain_pitch_mm,
        shaft_radius_mm=shaft_radius_mm,
    )

    outer_points = make_sprocket_profile_points(
        tooth_count,
        chain_pitch_mm,
        shaft_radius_mm,
    )
    segment_count = len(outer_points)
    top_z = cog_thickness_mm / 2.0
    bottom_z = -top_z

    inner_points: list[tuple[float, float]] = []
    for index in range(segment_count):
        angle_rad = 2.0 * math.pi * index / segment_count
        inner_points.append(
            (bore_radius_mm * math.cos(angle_rad), bore_radius_mm * math.sin(angle_rad))
        )

    x: list[float] = []
    y: list[float] = []
    z: list[float] = []

    def add_ring(points: list[tuple[float, float]], z_value: float) -> None:
        for point_x, point_y in points:
            x.append(point_x)
            y.append(point_y)
            z.append(z_value)

    add_ring(outer_points, top_z)
    add_ring(inner_points, top_z)
    add_ring(outer_points, bottom_z)
    add_ring(inner_points, bottom_z)

    outer_top = 0
    inner_top = segment_count
    outer_bottom = segment_count * 2
    inner_bottom = segment_count * 3

    i: list[int] = []
    j: list[int] = []
    k: list[int] = []

    def add_triangle(a: int, b: int, c: int) -> None:
        i.append(a)
        j.append(b)
        k.append(c)

    for index in range(segment_count):
        next_index = (index + 1) % segment_count

        ot0 = outer_top + index
        ot1 = outer_top + next_index
        it0 = inner_top + index
        it1 = inner_top + next_index
        ob0 = outer_bottom + index
        ob1 = outer_bottom + next_index
        ib0 = inner_bottom + index
        ib1 = inner_bottom + next_index

        add_triangle(ot0, ot1, it1)
        add_triangle(ot0, it1, it0)
        add_triangle(ob0, ib1, ob1)
        add_triangle(ob0, ib0, ib1)
        add_triangle(ot0, ob0, ob1)
        add_triangle(ot0, ob1, ot1)
        add_triangle(it0, ib1, ib0)
        add_triangle(it0, it1, ib1)

    return GearMeshData(
        x=x,
        y=y,
        z=z,
        i=i,
        j=j,
        k=k,
        pitch_radius_mm=pitch_radius_mm,
        root_radius_mm=root_radius_mm,
        outer_radius_mm=outer_radius_mm,
        bore_radius_mm=bore_radius_mm,
        segment_count=segment_count,
    )


def build_reference_gear_mesh_data(
    vertices: Sequence[Sequence[float]],
    faces: Sequence[Sequence[int]],
    tooth_count: int,
    chain_pitch_mm: float,
    shaft_radius_mm: float,
) -> GearMeshData:
    """Normalize a Blender-X-axis reference STL into the Plotly gear frame."""
    if len(vertices) == 0:
        raise ValueError("vertices must contain at least one XYZ point")
    if any(len(vertex) != 3 for vertex in vertices):
        raise ValueError("vertices must contain XYZ triplets")
    if any(len(face) != 3 for face in faces):
        raise ValueError("faces must contain triangle index triplets")

    pitch_radius_mm, root_radius_mm, outer_radius_mm, bore_radius_mm = (
        calculate_simplified_cog_radii(
            tooth_count=tooth_count,
            chain_pitch_mm=chain_pitch_mm,
            shaft_radius_mm=shaft_radius_mm,
        )
    )
    source_x = [float(vertex[0]) for vertex in vertices]
    source_y = [float(vertex[1]) for vertex in vertices]
    source_z = [float(vertex[2]) for vertex in vertices]
    center_x = (min(source_x) + max(source_x)) / 2.0
    center_y = (min(source_y) + max(source_y)) / 2.0
    center_z = (min(source_z) + max(source_z)) / 2.0
    native_radius = max(
        math.hypot(y - center_y, z - center_z)
        for y, z in zip(source_y, source_z)
    )
    if native_radius <= 0:
        raise ValueError("reference gear must have non-zero radial extent")
    scale_to_mm = outer_radius_mm / native_radius

    plot_x = [(y - center_y) * scale_to_mm for y in source_y]
    plot_y = [(z - center_z) * scale_to_mm for z in source_z]
    plot_z = [(x - center_x) * scale_to_mm for x in source_x]
    face_i = [int(face[0]) for face in faces]
    face_j = [int(face[1]) for face in faces]
    face_k = [int(face[2]) for face in faces]

    return GearMeshData(
        x=plot_x,
        y=plot_y,
        z=plot_z,
        i=face_i,
        j=face_j,
        k=face_k,
        pitch_radius_mm=pitch_radius_mm,
        root_radius_mm=root_radius_mm,
        outer_radius_mm=outer_radius_mm,
        bore_radius_mm=bore_radius_mm,
        segment_count=len(vertices),
    )
