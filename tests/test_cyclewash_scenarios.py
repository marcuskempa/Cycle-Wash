"""Tests for the fixed CycleWash operating scenarios."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import math
import unittest

from cyclewash_scenarios import SCENARIOS, calculate_scenario, scenario_by_name


class CycleWashScenarioTests(unittest.TestCase):
    def test_fixed_scenarios_match_the_approved_operating_points(self) -> None:
        expected = (
            ("Gentle", 45.0, 100.0, 0.25, 2.0, 0.025, 1.5),
            ("Normal", 60.0, 150.0, 0.35, 3.5, 0.040, 2.0),
            ("Heavy", 50.0, 180.0, 0.45, 5.0, 0.060, 2.5),
        )

        actual = tuple(
            (
                scenario.name,
                scenario.speed_rpm,
                scenario.human_power_w,
                scenario.fill_fraction,
                scenario.laundry_mass_kg,
                scenario.eccentricity_m,
                scenario.transient_factor,
            )
            for scenario in SCENARIOS
        )

        self.assertEqual(expected, actual)
        with self.assertRaises(FrozenInstanceError):
            SCENARIOS[0].speed_rpm = 1.0

    def test_lookup_returns_the_canonical_scenario(self) -> None:
        self.assertIs(SCENARIOS[1], scenario_by_name("Normal"))
        with self.assertRaises(ValueError):
            scenario_by_name("Delicate")

    def test_calculation_adds_the_unbalanced_laundry_load_to_the_shaft_check(self) -> None:
        result = calculate_scenario(scenario_by_name("Normal"))
        expected_force = (
            result.scenario.laundry_mass_kg
            * result.scenario.eccentricity_m
            * result.analytical.angular_speed_rad_s**2
        )
        expected_moment = (
            result.analytical.shaft_bending_moment_n_m
            + expected_force * result.inputs.shaft_reaction_overhang_m
        )
        expected_bending = (
            32.0
            * expected_moment
            / (math.pi * result.inputs.shaft_diameter_m**3)
        )
        expected_von_mises = math.sqrt(
            expected_bending**2 + 3.0 * result.analytical.shaft_torsional_shear_pa**2
        )

        self.assertAlmostEqual(expected_force, result.imbalance_force_n)
        self.assertAlmostEqual(
            expected_force * result.inputs.shaft_reaction_overhang_m,
            result.imbalance_moment_n_m,
        )
        self.assertAlmostEqual(expected_moment, result.total_moment_n_m)
        self.assertAlmostEqual(expected_bending, result.bending_stress_pa)
        self.assertAlmostEqual(expected_von_mises, result.von_mises_pa)
        self.assertAlmostEqual(
            result.inputs.shaft_material.yield_strength_pa / expected_von_mises,
            result.factor_of_safety,
        )
        self.assertGreater(result.total_moment_n_m, result.analytical.shaft_bending_moment_n_m)

    def test_phase_components_follow_the_rotating_force_definition(self) -> None:
        result = calculate_scenario(scenario_by_name("Heavy"))

        force_y, force_z = result.imbalance_components(math.pi / 2.0)

        self.assertAlmostEqual(0.0, force_y, places=10)
        self.assertAlmostEqual(result.imbalance_force_n, force_z)


if __name__ == "__main__":
    unittest.main()
