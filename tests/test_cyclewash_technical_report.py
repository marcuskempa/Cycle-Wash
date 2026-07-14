"""Tests for the immutable CycleWash technical-report dataset."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cyclewash_technical_report import build_report_document


REQUIRED_FORMULA_IDENTIFIERS = {
    "drivetrain_speed_ratio",
    "angular_speed_and_edge_velocity",
    "human_power_torque_and_chain_force",
    "water_volume_mass_and_weight",
    "water_pressure",
    "unbalanced_wet_laundry_load",
    "shaft_bending_and_torsion",
    "combined_stress_and_factor_of_safety",
}


class CycleWashTechnicalReportTests(unittest.TestCase):
    def test_report_contains_all_scenarios_and_selected_detail(self) -> None:
        document = build_report_document("Normal")

        self.assertEqual(
            ("Gentle", "Normal", "Heavy"),
            tuple(report.scenario.name for report in document.scenario_reports),
        )
        self.assertEqual("Normal", document.selected_report.scenario.name)
        self.assertAlmostEqual(60.0, document.drivetrain.target_drum_rpm)
        dimensions = " ".join(item.meaning for item in document.project_dimensions)
        self.assertIn("0.270 m", dimensions)
        self.assertIn("0.025 m", dimensions)
        with self.assertRaises(FrozenInstanceError):
            document.conclusion = "changed"

    def test_formula_catalogue_is_complete_and_educational(self) -> None:
        document = build_report_document("Heavy")
        identifiers = {formula.identifier for formula in document.formulas}

        self.assertTrue(REQUIRED_FORMULA_IDENTIFIERS.issubset(identifiers))
        for formula in document.formulas:
            self.assertTrue(formula.title)
            self.assertTrue(formula.latex)
            self.assertIn("<", formula.html)
            self.assertIn("=", formula.evaluated)
            self.assertTrue(formula.explanation)
            self.assertTrue(formula.symbols)
            for symbol in formula.symbols:
                self.assertTrue(symbol.symbol)
                self.assertTrue(symbol.meaning)
                self.assertTrue(symbol.unit)
                self.assertTrue(symbol.source)

        pressure_formula = next(
            formula for formula in document.formulas if formula.identifier == "water_pressure"
        )
        self.assertIn("g", {symbol.symbol for symbol in pressure_formula.symbols})

    def test_report_uses_analytical_provenance_when_no_exact_package_exists(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            document = build_report_document("Gentle", Path(temporary_directory))

        self.assertEqual("Analytical load estimate", document.selected_report.provenance)
        self.assertTrue(document.assumptions)
        self.assertTrue(document.limitations)
        self.assertTrue(document.engineering_interpretation)
        self.assertTrue(document.conclusion)
        self.assertIn("radians", document.units_note)


if __name__ == "__main__":
    unittest.main()
