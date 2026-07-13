"""CycleWash drivetrain calculator and CadQuery CAD exporter.

This script estimates a direct chain-drive ratio for a bicycle-powered,
horizontal-axis washer drum and generates a simple rear cog/shaft STL.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

from cyclewash_dimensions import (
    CHAIN_PITCH_M,
    DRUM_EFFECTIVE_RADIUS_M,
    GEAR_THICKNESS_M,
    GEAR_TOOTH_COUNT,
    SHAFT_DIAMETER_M,
    SHAFT_LENGTH_M,
)


DEFAULT_PEDAL_RPM = 60.0
DEFAULT_TARGET_DRUM_RPM = 60.0
DEFAULT_FRONT_TEETH = 34
DEFAULT_DRUM_RADIUS_M = DRUM_EFFECTIVE_RADIUS_M
DEFAULT_CHAIN_PITCH_MM = CHAIN_PITCH_M * 1000.0
DEFAULT_TARGET_VELOCITY_MIN = 1.5
DEFAULT_TARGET_VELOCITY_MAX = 2.0
DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parent / "cyclewash_rear_cog_and_shaft.stl"
DEFAULT_SHAFT_RADIUS_MM = SHAFT_DIAMETER_M * 500.0
DEFAULT_SHAFT_LENGTH_MM = SHAFT_LENGTH_M * 1000.0
DEFAULT_COG_THICKNESS_MM = GEAR_THICKNESS_M * 1000.0
DEFAULT_REFERENCE_REAR_TEETH = GEAR_TOOTH_COUNT
DEFAULT_MAX_DRUM_SPEED_ERROR_FRACTION = 0.10


@dataclass(frozen=True)
class DriveResult:
    """Computed engineering values for the CycleWash drivetrain."""

    pedal_rpm: float
    target_drum_rpm: float
    front_teeth: int
    drum_radius_m: float
    chain_pitch_mm: float
    required_ratio: float
    exact_rear_teeth: float
    calculated_rear_teeth: int
    practical_rear_teeth: int
    gear_source: str
    actual_ratio: float
    actual_drum_rpm: float
    drum_speed_error_percent: float
    required_pedal_rpm_for_target: float
    fluid_velocity_m_s: float
    target_velocity_min: float
    target_velocity_max: float
    is_velocity_in_target: bool
    velocity_status: str


def validate_positive(name: str, value: float) -> float:
    """Return a numeric value after confirming it is positive."""
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def calculate_cyclewash_drive(
    pedal_rpm: float = DEFAULT_PEDAL_RPM,
    target_drum_rpm: float = DEFAULT_TARGET_DRUM_RPM,
    front_teeth: int = DEFAULT_FRONT_TEETH,
    drum_radius_m: float = DEFAULT_DRUM_RADIUS_M,
    chain_pitch_mm: float = DEFAULT_CHAIN_PITCH_MM,
    target_velocity_min: float = DEFAULT_TARGET_VELOCITY_MIN,
    target_velocity_max: float = DEFAULT_TARGET_VELOCITY_MAX,
    reference_rear_teeth: int | None = DEFAULT_REFERENCE_REAR_TEETH,
    max_drum_speed_error_fraction: float = DEFAULT_MAX_DRUM_SPEED_ERROR_FRACTION,
    prefer_reference_gear: bool = True,
) -> DriveResult:
    """Calculate drivetrain values and select a reference or calculated cog."""
    validate_positive("pedal_rpm", pedal_rpm)
    validate_positive("target_drum_rpm", target_drum_rpm)
    validate_positive("front_teeth", front_teeth)
    validate_positive("drum_radius_m", drum_radius_m)
    validate_positive("chain_pitch_mm", chain_pitch_mm)
    validate_positive("target_velocity_min", target_velocity_min)
    validate_positive("target_velocity_max", target_velocity_max)
    if target_velocity_min > target_velocity_max:
        raise ValueError("target_velocity_min must be less than or equal to target_velocity_max")
    if max_drum_speed_error_fraction < 0:
        raise ValueError("max_drum_speed_error_fraction must be non-negative")
    if reference_rear_teeth is not None and reference_rear_teeth < 3:
        raise ValueError("reference_rear_teeth must be at least 3")

    required_ratio = target_drum_rpm / pedal_rpm
    exact_rear_teeth = front_teeth * pedal_rpm / target_drum_rpm
    calculated_rear_teeth = max(3, round(exact_rear_teeth))

    def candidate_values(rear_teeth: int) -> tuple[float, float, float]:
        actual_drum = pedal_rpm * front_teeth / rear_teeth
        speed_error = abs(actual_drum - target_drum_rpm) / target_drum_rpm
        edge_velocity = 2.0 * math.pi * actual_drum / 60.0 * drum_radius_m
        return actual_drum, speed_error, edge_velocity

    practical_rear_teeth = calculated_rear_teeth
    gear_source = "calculated"
    if prefer_reference_gear and reference_rear_teeth is not None:
        reference_drum_rpm, reference_speed_error, reference_velocity = candidate_values(
            reference_rear_teeth
        )
        reference_is_acceptable = (
            reference_speed_error <= max_drum_speed_error_fraction
            and target_velocity_min <= reference_velocity <= target_velocity_max
        )
        if reference_is_acceptable:
            practical_rear_teeth = reference_rear_teeth
            gear_source = "reference_stl"

    actual_drum_rpm = pedal_rpm * front_teeth / practical_rear_teeth
    actual_ratio = front_teeth / practical_rear_teeth
    drum_speed_error_percent = (
        (actual_drum_rpm - target_drum_rpm) / target_drum_rpm * 100.0
    )
    required_pedal_rpm_for_target = (
        target_drum_rpm * practical_rear_teeth / front_teeth
    )
    angular_velocity_rad_s = 2.0 * math.pi * actual_drum_rpm / 60.0
    fluid_velocity_m_s = angular_velocity_rad_s * drum_radius_m

    if fluid_velocity_m_s < target_velocity_min:
        velocity_status = "below target"
    elif fluid_velocity_m_s > target_velocity_max:
        velocity_status = "above target"
    else:
        velocity_status = "within target"

    return DriveResult(
        pedal_rpm=pedal_rpm,
        target_drum_rpm=target_drum_rpm,
        front_teeth=int(front_teeth),
        drum_radius_m=drum_radius_m,
        chain_pitch_mm=chain_pitch_mm,
        required_ratio=required_ratio,
        exact_rear_teeth=exact_rear_teeth,
        calculated_rear_teeth=calculated_rear_teeth,
        practical_rear_teeth=practical_rear_teeth,
        gear_source=gear_source,
        actual_ratio=actual_ratio,
        actual_drum_rpm=actual_drum_rpm,
        drum_speed_error_percent=drum_speed_error_percent,
        required_pedal_rpm_for_target=required_pedal_rpm_for_target,
        fluid_velocity_m_s=fluid_velocity_m_s,
        target_velocity_min=target_velocity_min,
        target_velocity_max=target_velocity_max,
        is_velocity_in_target=target_velocity_min <= fluid_velocity_m_s <= target_velocity_max,
        velocity_status=velocity_status,
    )


def calculate_pitch_radius_mm(tooth_count: int, chain_pitch_mm: float) -> float:
    """Calculate sprocket pitch radius from tooth count and chain pitch."""
    validate_positive("chain_pitch_mm", chain_pitch_mm)
    if tooth_count < 3:
        raise ValueError("tooth_count must be at least 3")
    return chain_pitch_mm / (2.0 * math.sin(math.pi / tooth_count))


def calculate_sprocket_radii_mm(
    tooth_count: int,
    chain_pitch_mm: float,
    shaft_radius_mm: float,
) -> tuple[float, float, float, float]:
    """Return pitch, root, outer, and bore radii for the teaching sprocket."""
    validate_positive("shaft_radius_mm", shaft_radius_mm)
    pitch_radius_mm = calculate_pitch_radius_mm(tooth_count, chain_pitch_mm)
    root_radius_mm = max(
        shaft_radius_mm * 1.8,
        pitch_radius_mm - chain_pitch_mm * 0.30,
    )
    outer_radius_mm = pitch_radius_mm + chain_pitch_mm * 0.12
    bore_radius_mm = shaft_radius_mm * 1.05
    if bore_radius_mm >= root_radius_mm or root_radius_mm >= outer_radius_mm:
        raise ValueError("shaft_radius_mm is too large for the simplified cog geometry")
    return pitch_radius_mm, root_radius_mm, outer_radius_mm, bore_radius_mm


def make_sprocket_profile_points(
    tooth_count: int,
    chain_pitch_mm: float,
    shaft_radius_mm: float,
) -> list[tuple[float, float]]:
    """Create a deeper six-sample-per-tooth educational sprocket profile."""
    pitch_radius_mm, root_radius_mm, outer_radius_mm, _ = calculate_sprocket_radii_mm(
        tooth_count,
        chain_pitch_mm,
        shaft_radius_mm,
    )
    shoulder_radius_mm = (root_radius_mm + pitch_radius_mm) / 2.0
    tooth_radii = (
        root_radius_mm,
        shoulder_radius_mm,
        outer_radius_mm,
        outer_radius_mm,
        shoulder_radius_mm,
        root_radius_mm,
    )

    points: list[tuple[float, float]] = []
    segment_count = tooth_count * len(tooth_radii)
    for index in range(segment_count):
        angle_rad = 2.0 * math.pi * index / segment_count
        radius_mm = tooth_radii[index % len(tooth_radii)]
        points.append((radius_mm * math.cos(angle_rad), radius_mm * math.sin(angle_rad)))
    return points


def build_cyclewash_cad(
    result: DriveResult,
    shaft_radius_mm: float = DEFAULT_SHAFT_RADIUS_MM,
    shaft_length_mm: float = DEFAULT_SHAFT_LENGTH_MM,
    cog_thickness_mm: float = DEFAULT_COG_THICKNESS_MM,
):
    """Build a simplified CadQuery shaft and rear cog model."""
    validate_positive("shaft_radius_mm", shaft_radius_mm)
    validate_positive("shaft_length_mm", shaft_length_mm)
    validate_positive("cog_thickness_mm", cog_thickness_mm)

    try:
        import cadquery as cq
    except ImportError as error:
        raise ImportError(
            "CadQuery is required for STL export. Install it with: pip install cadquery"
        ) from error

    tooth_points = make_sprocket_profile_points(
        tooth_count=result.practical_rear_teeth,
        chain_pitch_mm=result.chain_pitch_mm,
        shaft_radius_mm=shaft_radius_mm,
    )

    shaft = (
        cq.Workplane("XY")
        .circle(shaft_radius_mm)
        .extrude(shaft_length_mm)
        .translate((0, 0, -shaft_length_mm / 2.0))
    )

    bore_radius_mm = shaft_radius_mm * 1.05
    cog_blank = (
        cq.Workplane("XY")
        .polyline(tooth_points)
        .close()
        .extrude(cog_thickness_mm)
        .translate((0, 0, -cog_thickness_mm / 2.0))
    )
    bore = (
        cq.Workplane("XY")
        .circle(bore_radius_mm)
        .extrude(cog_thickness_mm + 2.0)
        .translate((0, 0, -(cog_thickness_mm + 2.0) / 2.0))
    )
    rear_cog = cog_blank.cut(bore)

    return shaft.union(rear_cog)


def export_cyclewash_stl(
    result: DriveResult,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    shaft_radius_mm: float = DEFAULT_SHAFT_RADIUS_MM,
    shaft_length_mm: float = DEFAULT_SHAFT_LENGTH_MM,
    cog_thickness_mm: float = DEFAULT_COG_THICKNESS_MM,
) -> Path:
    """Export the CadQuery model as an STL file and return its path."""
    try:
        from cadquery import exporters
    except ImportError as error:
        raise ImportError(
            "CadQuery is required for STL export. Install it with: pip install cadquery"
        ) from error

    model = build_cyclewash_cad(
        result=result,
        shaft_radius_mm=shaft_radius_mm,
        shaft_length_mm=shaft_length_mm,
        cog_thickness_mm=cog_thickness_mm,
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    exporters.export(model, str(output_path))
    return output_path


def format_engineering_summary(result: DriveResult) -> str:
    """Format a readable terminal summary for the CycleWash drivetrain."""
    pitch_radius_mm = calculate_pitch_radius_mm(
        result.practical_rear_teeth,
        result.chain_pitch_mm,
    )
    gear_source_label = (
        "Existing 32T STL"
        if result.gear_source == "reference_stl"
        else "Calculated sprocket"
    )
    return f"""
CycleWash Engineering Summary
=============================

Inputs
------
Target pedaling cadence:       {result.pedal_rpm:8.2f} rpm
Target drum speed:             {result.target_drum_rpm:8.2f} rpm
Front chainring:               {result.front_teeth:8d} teeth
STL-derived effective fluid radius: {result.drum_radius_m:8.3f} m
Bicycle chain pitch:           {result.chain_pitch_mm:8.2f} mm

Gear Ratio And Cog Sizing
-------------------------
Required drum/pedal speed ratio: {result.required_ratio:8.4f}:1
Actual drum/pedal speed ratio:   {result.actual_ratio:8.4f}:1
Exact rear cog size:             {result.exact_rear_teeth:8.2f} teeth
Calculated rear cog size:        {result.calculated_rear_teeth:8d} teeth
Practical rear cog size:         {result.practical_rear_teeth:8d} teeth
Selected gear source:            {gear_source_label}
Actual drum speed with cog:      {result.actual_drum_rpm:8.2f} rpm
Drum speed deviation:            {result.drum_speed_error_percent:+8.2f}%
Cadence for target drum speed:   {result.required_pedal_rpm_for_target:8.2f} rpm
Rear cog pitch radius:           {pitch_radius_mm:8.2f} mm

Fluid Velocity Check
--------------------
Drum-edge fluid velocity:        {result.fluid_velocity_m_s:8.3f} m/s
Target velocity range:           {result.target_velocity_min:8.2f} to {result.target_velocity_max:.2f} m/s
Result:                          {result.velocity_status.upper()}
""".strip()


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Calculate CycleWash drivetrain values and export a simple CadQuery STL.",
    )
    parser.add_argument("--pedal-rpm", type=float, default=DEFAULT_PEDAL_RPM)
    parser.add_argument("--target-drum-rpm", type=float, default=DEFAULT_TARGET_DRUM_RPM)
    parser.add_argument("--front-teeth", type=int, default=DEFAULT_FRONT_TEETH)
    parser.add_argument("--drum-radius-m", type=float, default=DEFAULT_DRUM_RADIUS_M)
    parser.add_argument("--chain-pitch-mm", type=float, default=DEFAULT_CHAIN_PITCH_MM)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--shaft-radius-mm", type=float, default=DEFAULT_SHAFT_RADIUS_MM)
    parser.add_argument("--shaft-length-mm", type=float, default=DEFAULT_SHAFT_LENGTH_MM)
    parser.add_argument("--cog-thickness-mm", type=float, default=DEFAULT_COG_THICKNESS_MM)
    parser.add_argument(
        "--skip-cad",
        action="store_true",
        help="Print calculations only; do not attempt CadQuery STL export.",
    )
    return parser


def main() -> int:
    """Run the calculator from the command line."""
    parser = build_parser()
    args = parser.parse_args()

    try:
        result = calculate_cyclewash_drive(
            pedal_rpm=args.pedal_rpm,
            target_drum_rpm=args.target_drum_rpm,
            front_teeth=args.front_teeth,
            drum_radius_m=args.drum_radius_m,
            chain_pitch_mm=args.chain_pitch_mm,
        )
    except ValueError as error:
        parser.error(str(error))

    print(format_engineering_summary(result))

    if not args.skip_cad:
        print()
        try:
            output_path = export_cyclewash_stl(
                result=result,
                output_path=args.output,
                shaft_radius_mm=args.shaft_radius_mm,
                shaft_length_mm=args.shaft_length_mm,
                cog_thickness_mm=args.cog_thickness_mm,
            )
        except ImportError as error:
            print(error)
            print("Use --skip-cad to run calculations without exporting geometry.")
            return 1
        except ValueError as error:
            parser.error(str(error))
        print(f"STL exported: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
