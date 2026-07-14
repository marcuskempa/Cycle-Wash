"""Fixed CycleWash operating scenarios and unbalanced-laundry calculations."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Final

from cyclewash_engineering_model import (
    AnalyticalResults,
    EngineeringInputs,
    calculate_engineering_loads,
)


@dataclass(frozen=True)
class OperatingScenario:
    """One approved, non-editable CycleWash operating point."""

    name: str
    speed_rpm: float
    human_power_w: float
    fill_fraction: float
    laundry_mass_kg: float
    eccentricity_m: float
    transient_factor: float

    def engineering_inputs(self) -> EngineeringInputs:
        """Return the existing FEA-compatible inputs for this scenario."""

        return EngineeringInputs(
            human_power_w=self.human_power_w,
            speed_rpm=self.speed_rpm,
            transient_factor=self.transient_factor,
            fill_fraction=self.fill_fraction,
        )


@dataclass(frozen=True)
class ScenarioResults:
    """Base analytical loads plus the scenario-specific laundry imbalance."""

    scenario: OperatingScenario
    inputs: EngineeringInputs
    analytical: AnalyticalResults
    imbalance_force_n: float
    imbalance_moment_n_m: float
    total_moment_n_m: float
    bending_stress_pa: float
    von_mises_pa: float
    factor_of_safety: float

    def imbalance_components(self, phase_rad: float) -> tuple[float, float]:
        """Return the rotating unbalanced-force components in the y-z plane."""

        return phase_resolved_imbalance_components(self.imbalance_force_n, phase_rad)


SCENARIOS: Final[tuple[OperatingScenario, ...]] = (
    OperatingScenario("Gentle", 45.0, 100.0, 0.25, 2.0, 0.025, 1.5),
    OperatingScenario("Normal", 60.0, 150.0, 0.35, 3.5, 0.040, 2.0),
    OperatingScenario("Heavy", 50.0, 180.0, 0.45, 5.0, 0.060, 2.5),
)


def scenario_by_name(name: str) -> OperatingScenario:
    """Return an approved scenario by its display name, case-insensitively."""

    if not isinstance(name, str):
        raise ValueError("scenario name must be a string")
    requested_name = name.strip().casefold()
    for scenario in SCENARIOS:
        if scenario.name.casefold() == requested_name:
            return scenario
    available = ", ".join(scenario.name for scenario in SCENARIOS)
    raise ValueError(f"Unknown CycleWash scenario {name!r}; choose one of: {available}")


def phase_resolved_imbalance_components(
    imbalance_force_n: float, phase_rad: float
) -> tuple[float, float]:
    """Resolve the force magnitude using F_y=F_u cos(theta), F_z=F_u sin(theta)."""

    return (
        imbalance_force_n * math.cos(phase_rad),
        imbalance_force_n * math.sin(phase_rad),
    )


def calculate_scenario(scenario: OperatingScenario) -> ScenarioResults:
    """Evaluate one fixed scenario without altering the base FEA input schema."""

    if not isinstance(scenario, OperatingScenario):
        raise TypeError("scenario must be an OperatingScenario")

    inputs = scenario.engineering_inputs()
    analytical = calculate_engineering_loads(inputs)
    imbalance_force_n = (
        scenario.laundry_mass_kg
        * scenario.eccentricity_m
        * analytical.angular_speed_rad_s**2
    )
    imbalance_moment_n_m = imbalance_force_n * inputs.shaft_reaction_overhang_m
    total_moment_n_m = analytical.shaft_bending_moment_n_m + imbalance_moment_n_m
    bending_stress_pa = (
        32.0 * total_moment_n_m / (math.pi * inputs.shaft_diameter_m**3)
    )
    von_mises_pa = math.sqrt(
        bending_stress_pa**2 + 3.0 * analytical.shaft_torsional_shear_pa**2
    )
    factor_of_safety = inputs.shaft_material.yield_strength_pa / von_mises_pa
    return ScenarioResults(
        scenario=scenario,
        inputs=inputs,
        analytical=analytical,
        imbalance_force_n=imbalance_force_n,
        imbalance_moment_n_m=imbalance_moment_n_m,
        total_moment_n_m=total_moment_n_m,
        bending_stress_pa=bending_stress_pa,
        von_mises_pa=von_mises_pa,
        factor_of_safety=factor_of_safety,
    )


__all__ = [
    "OperatingScenario",
    "SCENARIOS",
    "ScenarioResults",
    "calculate_scenario",
    "phase_resolved_imbalance_components",
    "scenario_by_name",
]
