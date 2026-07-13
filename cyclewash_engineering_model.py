"""Analytical engineering model for the CycleWash Stage 1 analysis.

All calculations use SI units.  The model provides transparent hand-calculation
cross-checks for the optional linear-static FEA; it is not a CFD solver.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Sequence

from cyclewash_dimensions import (
    DRUM_DEPTH_M,
    DRUM_EFFECTIVE_RADIUS_M,
    GEAR_PITCH_RADIUS_M,
    GEAR_THICKNESS_M,
    SHAFT_DIAMETER_M,
    SHAFT_LENGTH_M,
)


SHAFT_LOAD_BAND_WIDTH_M = 0.012
_MESH_LEVEL_ORDER = {"coarse": 0, "medium": 1, "fine": 2}


@dataclass(frozen=True)
class MaterialProperties:
    """Isotropic material values used by analytical and FEA models."""

    name: str
    youngs_modulus_pa: float
    poisson_ratio: float
    density_kg_m3: float
    yield_strength_pa: float


GALVANIZED_STEEL = MaterialProperties(
    name="Galvanized steel",
    youngs_modulus_pa=200.0e9,
    poisson_ratio=0.30,
    density_kg_m3=7_850.0,
    yield_strength_pa=250.0e6,
)

STAINLESS_STEEL = MaterialProperties(
    name="Stainless steel",
    youngs_modulus_pa=193.0e9,
    poisson_ratio=0.29,
    density_kg_m3=8_000.0,
    yield_strength_pa=215.0e6,
)


@dataclass(frozen=True)
class EngineeringInputs:
    """Parametric operating, geometry, load, and material inputs."""

    human_power_w: float = 150.0
    speed_rpm: float = 60.0
    transient_factor: float = 2.0
    enclosure_width_m: float = 0.684
    enclosure_body_height_m: float = 0.680
    overall_height_m: float = 0.737
    shaft_diameter_m: float = SHAFT_DIAMETER_M
    shaft_length_m: float = SHAFT_LENGTH_M
    chain_force_overhang_m: float = 0.070
    shaft_transverse_reaction_n: float = 0.0
    shaft_reaction_overhang_m: float = 0.070
    gear_pitch_radius_m: float = GEAR_PITCH_RADIUS_M
    gear_sprocket_thickness_m: float = GEAR_THICKNESS_M
    gear_hub_radius_m: float = 0.020
    gear_hub_thickness_m: float = 0.0375
    drum_radius_m: float = DRUM_EFFECTIVE_RADIUS_M
    drum_depth_m: float = DRUM_DEPTH_M
    drum_wall_thickness_m: float = 0.003
    fill_fraction: float = 0.35
    perforation_relief: float = 0.45
    perforation_open_area_ratio: float = 0.18
    drum_stiffness_factor: float = 0.75
    slosh_amplification: float = 1.30
    water_density_kg_m3: float = 1_000.0
    gravity_m_s2: float = 9.80665
    shaft_material: MaterialProperties = GALVANIZED_STEEL
    drum_material: MaterialProperties = STAINLESS_STEEL

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible input mapping."""

        return asdict(self)


@dataclass(frozen=True)
class AnalyticalResults:
    """Evaluated operating loads and classical shaft-stress cross-checks."""

    angular_speed_rad_s: float
    nominal_torque_n_m: float
    design_torque_n_m: float
    chain_force_n: float
    full_drum_volume_m3: float
    retained_water_volume_m3: float
    retained_water_mass_kg: float
    retained_water_weight_n: float
    maximum_water_depth_m: float
    hydrostatic_pressure_pa: float
    centrifugal_pressure_pa: float
    design_water_pressure_pa: float
    shaft_transverse_reaction_n: float
    shaft_chain_bending_moment_n_m: float
    shaft_reaction_bending_moment_n_m: float
    shaft_bending_moment_n_m: float
    shaft_bending_stress_pa: float
    shaft_torsional_shear_pa: float
    shaft_von_mises_pa: float
    shaft_factor_of_safety: float


_POSITIVE_INPUTS = (
    "human_power_w",
    "speed_rpm",
    "transient_factor",
    "enclosure_width_m",
    "enclosure_body_height_m",
    "overall_height_m",
    "shaft_diameter_m",
    "shaft_length_m",
    "chain_force_overhang_m",
    "shaft_reaction_overhang_m",
    "gear_pitch_radius_m",
    "gear_sprocket_thickness_m",
    "gear_hub_radius_m",
    "gear_hub_thickness_m",
    "drum_radius_m",
    "drum_depth_m",
    "drum_wall_thickness_m",
    "drum_stiffness_factor",
    "slosh_amplification",
    "water_density_kg_m3",
    "gravity_m_s2",
)

_NONNEGATIVE_INPUTS = ("shaft_transverse_reaction_n",)

_FRACTION_INPUTS = (
    "fill_fraction",
    "perforation_relief",
    "perforation_open_area_ratio",
)


def _validate_material(field_name: str, material: MaterialProperties) -> None:
    for property_name in (
        "youngs_modulus_pa",
        "density_kg_m3",
        "yield_strength_pa",
    ):
        value = getattr(material, property_name)
        if not math.isfinite(value):
            raise ValueError(
                f"{field_name}.{property_name} must be finite and positive"
            )
        if value <= 0.0:
            raise ValueError(f"{field_name}.{property_name} must be positive")
    if not math.isfinite(material.poisson_ratio) or not (
        0.0 < material.poisson_ratio < 0.5
    ):
        raise ValueError(f"{field_name}.poisson_ratio must be between 0 and 0.5")


def _validate_inputs(inputs: EngineeringInputs) -> None:
    for name in _POSITIVE_INPUTS:
        value = getattr(inputs, name)
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite and positive")
        if value <= 0.0:
            raise ValueError(f"{name} must be positive")
    for name in _NONNEGATIVE_INPUTS:
        value = getattr(inputs, name)
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f"{name} must be non-negative")
    for name in _FRACTION_INPUTS:
        value = getattr(inputs, name)
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be between 0 and 1")
    _validate_material("shaft_material", inputs.shaft_material)
    _validate_material("drum_material", inputs.drum_material)
    if inputs.overall_height_m < inputs.enclosure_body_height_m:
        raise ValueError("overall_height_m must be at least enclosure_body_height_m")
    if inputs.gear_hub_radius_m <= inputs.shaft_diameter_m / 2.0:
        raise ValueError("gear_hub_radius_m must exceed the shaft radius")
    if inputs.gear_hub_radius_m >= inputs.gear_pitch_radius_m:
        raise ValueError("gear_hub_radius_m must be smaller than gear_pitch_radius_m")
    if inputs.gear_hub_thickness_m < inputs.gear_sprocket_thickness_m:
        raise ValueError(
            "gear_hub_thickness_m must be at least gear_sprocket_thickness_m"
        )
    half_band = SHAFT_LOAD_BAND_WIDTH_M / 2.0
    if not half_band < inputs.chain_force_overhang_m < inputs.shaft_length_m - half_band:
        raise ValueError(
            "chain force load band must fit within shaft_length_m; complete shaft load band is required"
        )
    if not half_band < inputs.shaft_reaction_overhang_m < inputs.shaft_length_m - half_band:
        raise ValueError("shaft reaction load band must fit within shaft_length_m")


def calculate_engineering_loads(inputs: EngineeringInputs) -> AnalyticalResults:
    """Evaluate CycleWash operating loads and solid-shaft stresses."""

    _validate_inputs(inputs)

    omega = 2.0 * math.pi * inputs.speed_rpm / 60.0
    nominal_torque = inputs.human_power_w / omega
    design_torque = nominal_torque * inputs.transient_factor
    chain_force = design_torque / inputs.gear_pitch_radius_m

    full_volume = math.pi * inputs.drum_radius_m**2 * inputs.drum_depth_m
    retained_volume = (
        full_volume * inputs.fill_fraction * (1.0 - inputs.perforation_relief)
    )
    retained_mass = retained_volume * inputs.water_density_kg_m3
    retained_weight = retained_mass * inputs.gravity_m_s2

    maximum_depth = 2.0 * inputs.drum_radius_m * inputs.fill_fraction
    hydrostatic_pressure = (
        inputs.water_density_kg_m3
        * inputs.gravity_m_s2
        * maximum_depth
        * inputs.slosh_amplification
    )
    centrifugal_pressure = (
        0.5
        * inputs.water_density_kg_m3
        * omega**2
        * inputs.drum_radius_m**2
        * inputs.fill_fraction
        * (1.0 - inputs.perforation_relief)
    )
    design_water_pressure = hydrostatic_pressure + centrifugal_pressure

    chain_bending_moment = chain_force * inputs.chain_force_overhang_m
    reaction_bending_moment = (
        inputs.shaft_transverse_reaction_n * inputs.shaft_reaction_overhang_m
    )
    bending_moment = chain_bending_moment + reaction_bending_moment
    diameter_cubed = inputs.shaft_diameter_m**3
    bending_stress = 32.0 * bending_moment / (math.pi * diameter_cubed)
    torsional_shear = 16.0 * design_torque / (math.pi * diameter_cubed)
    von_mises = math.sqrt(bending_stress**2 + 3.0 * torsional_shear**2)
    factor_of_safety = inputs.shaft_material.yield_strength_pa / von_mises

    return AnalyticalResults(
        angular_speed_rad_s=omega,
        nominal_torque_n_m=nominal_torque,
        design_torque_n_m=design_torque,
        chain_force_n=chain_force,
        full_drum_volume_m3=full_volume,
        retained_water_volume_m3=retained_volume,
        retained_water_mass_kg=retained_mass,
        retained_water_weight_n=retained_weight,
        maximum_water_depth_m=maximum_depth,
        hydrostatic_pressure_pa=hydrostatic_pressure,
        centrifugal_pressure_pa=centrifugal_pressure,
        design_water_pressure_pa=design_water_pressure,
        shaft_transverse_reaction_n=inputs.shaft_transverse_reaction_n,
        shaft_chain_bending_moment_n_m=chain_bending_moment,
        shaft_reaction_bending_moment_n_m=reaction_bending_moment,
        shaft_bending_moment_n_m=bending_moment,
        shaft_bending_stress_pa=bending_stress,
        shaft_torsional_shear_pa=torsional_shear,
        shaft_von_mises_pa=von_mises,
        shaft_factor_of_safety=factor_of_safety,
    )


def normalize_mesh_levels(mesh_levels: Sequence[str]) -> tuple[str, ...]:
    """Validate, deduplicate, and order requested solver mesh levels."""

    if isinstance(mesh_levels, (str, bytes)):
        raise ValueError("mesh_levels must be a sequence of non-empty strings")
    normalized: set[str] = set()
    for level in mesh_levels:
        if not isinstance(level, str) or not level.strip():
            raise ValueError("mesh_levels must contain at least one non-empty string")
        candidate = level.strip().lower()
        if candidate not in _MESH_LEVEL_ORDER:
            raise ValueError(f"unsupported mesh level: {level!r}")
        normalized.add(candidate)
    if not normalized:
        raise ValueError("mesh_levels must contain at least one non-empty string")
    return tuple(sorted(normalized, key=_MESH_LEVEL_ORDER.__getitem__))


def canonical_request_identity(
    inputs: EngineeringInputs, mesh_levels: Sequence[str]
) -> dict[str, Any]:
    """Return stable request metadata shared by the solver and runner."""

    calculate_engineering_loads(inputs)
    levels = normalize_mesh_levels(mesh_levels)
    input_mapping = inputs.to_dict()
    input_json = json.dumps(
        input_mapping, allow_nan=False, separators=(",", ":"), sort_keys=True
    )
    request_json = json.dumps(
        {"engineering_inputs": input_mapping, "mesh_levels": levels},
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return {
        "input_sha256": hashlib.sha256(input_json.encode("utf-8")).hexdigest(),
        "mesh_levels": list(levels),
        "request_sha256": hashlib.sha256(request_json.encode("utf-8")).hexdigest(),
    }


def format_fea_engineering_summary(
    inputs: EngineeringInputs,
    analytical: AnalyticalResults,
    package_summary: str | None = None,
) -> str:
    """Format an evaluated, copyable engineering report in plain text."""

    package_text = package_summary or "No solved FEA package loaded; analytical preview shown."
    return f"""CycleWash Stage 1 Structural Analysis
=====================================

Scope
-----
Linear-static FEA with reduced-order water loading; not CFD.
{package_text}

Assumptions
-----------
- Enclosure: {inputs.enclosure_width_m:.3f} m wide, {inputs.enclosure_body_height_m:.3f} m body height, {inputs.overall_height_m:.3f} m overall height (reported, not solved).
- Gear: {inputs.gear_sprocket_thickness_m:.4f} m sprocket, {inputs.gear_hub_radius_m:.4f} m hub radius, {inputs.gear_hub_thickness_m:.4f} m hub thickness.
- Human input is {inputs.human_power_w:.1f} W at {inputs.speed_rpm:.1f} RPM.
- A transient design factor of {inputs.transient_factor:.2f} multiplies torque.
- The model applies {inputs.perforation_relief:.0%} perforation relief to retained mass and centrifugal pressure.
- It applies {inputs.slosh_amplification:.2f} slosh amplification to hydrostatic pressure.
- Materials are homogeneous, isotropic, and linearly elastic.

Operating Loads
---------------
ω = 2πN/60 = {analytical.angular_speed_rad_s:.4f} rad/s
T_nom = P/ω = {analytical.nominal_torque_n_m:.3f} N·m
T_design = K_t T_nom = {analytical.design_torque_n_m:.3f} N·m
F_chain = T_design/r_g = {analytical.chain_force_n:.1f} N
F_reaction = {analytical.shaft_transverse_reaction_n:.1f} N at {inputs.shaft_reaction_overhang_m:.3f} m

Water Model
-----------
V_drum = πR²L = {analytical.full_drum_volume_m3:.6f} m³
m_water = ρV f_fill(1-r_relief) = {analytical.retained_water_mass_kg:.2f} kg
p_h = ρghK_slosh = {analytical.hydrostatic_pressure_pa:.1f} Pa
p_c = ρ ω²R² f_fill(1-r_relief)/2 = {analytical.centrifugal_pressure_pa:.1f} Pa
p_design = p_h + p_c = {analytical.design_water_pressure_pa:.1f} Pa

Shaft Cross-Check
-----------------
M_chain = F_chain e_chain = {analytical.shaft_chain_bending_moment_n_m:.3f} N*m
M_reaction = F_reaction e_reaction = {analytical.shaft_reaction_bending_moment_n_m:.3f} N*m
M_total = M_chain + M_reaction = {analytical.shaft_bending_moment_n_m:.3f} N*m
M = F_chain e = {analytical.shaft_bending_moment_n_m:.3f} N·m
σ_b = 32M/(πd³) = {analytical.shaft_bending_stress_pa:.3e} Pa
τ_t = 16T/(πd³) = {analytical.shaft_torsional_shear_pa:.3e} Pa
σ_vm = √(σ_b² + 3τ_t²) = {analytical.shaft_von_mises_pa:.3e} Pa
FoS_y = S_y/σ_vm = {analytical.shaft_factor_of_safety:.3f}
""".strip()
