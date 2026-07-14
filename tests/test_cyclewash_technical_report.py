"""Tests for the immutable CycleWash technical-report dataset."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cyclewash_fea_runner import solver_request_hash
from cyclewash_scenarios import scenario_by_name
from cyclewash_technical_report import (
    CANONICAL_FEA_MESH_LEVELS,
    build_report_document,
)
from tests.test_cyclewash_fea_results import EXACT_PACKAGE_HASH, snapshot_tree


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

EXPECTED_FORMULA_SYMBOLS = {
    "drivetrain_speed_ratio": {
        "N_d,practical",
        "N_d,target",
        "N_p",
        "T_f",
        "T_r",
        "T_r,ideal",
    },
    "angular_speed_and_edge_velocity": {"omega", "N_d,target", "R", "v_edge"},
    "human_power_torque_and_chain_force": {
        "P",
        "T_nom",
        "K_t",
        "T_design",
        "F_chain",
        "r_g",
        "omega",
    },
    "water_volume_mass_and_weight": {
        "V_drum",
        "V_retained",
        "R",
        "L",
        "f_fill",
        "r_relief",
        "rho",
        "m_water",
        "g",
        "W_water",
    },
    "water_pressure": {
        "p_h",
        "p_c",
        "p_design",
        "h",
        "K_s",
        "rho",
        "g",
        "omega",
        "R",
        "f_fill",
        "r_relief",
    },
    "unbalanced_wet_laundry_load": {
        "F_u",
        "m_u",
        "e",
        "omega",
        "F_y",
        "F_z",
        "theta",
        "t",
        "theta_0",
    },
    "shaft_bending_and_torsion": {
        "M_chain",
        "M_reaction",
        "M_u",
        "M_total",
        "F_chain",
        "F_reaction",
        "F_u",
        "e_chain",
        "e_reaction",
        "d",
        "sigma_b",
        "tau_t",
        "T_design",
    },
    "combined_stress_and_factor_of_safety": {
        "sigma_vm",
        "sigma_b",
        "tau_t",
        "FoS_y",
        "S_y",
    },
}


class CycleWashTechnicalReportTests(unittest.TestCase):
    def test_default_cache_is_not_attached_to_gentle_or_heavy_reports(self) -> None:
        project_root = Path(__file__).resolve().parents[1]

        for selected_name in ("Gentle", "Heavy"):
            with self.subTest(selected_name=selected_name):
                document = build_report_document(
                    selected_name, project_root / "fea_results"
                )

                self.assertEqual(selected_name, document.selected_report.scenario.name)
                self.assertEqual(
                    "Analytical load estimate", document.selected_report.provenance
                )
                self.assertTrue(
                    all(report.fea_package is None for report in document.scenario_reports)
                )
                self.assertTrue(
                    all(not report.fea_components for report in document.scenario_reports)
                )
                self.assertTrue(
                    all(not report.fea_summary for report in document.scenario_reports)
                )
                self.assertNotIn(
                    "fea_result_definitions",
                    {formula.identifier for formula in document.formulas},
                )

    def test_normal_uses_the_exact_default_coarse_cache_without_solving(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        normal_inputs = scenario_by_name("Normal").engineering_inputs()

        with patch(
            "cyclewash_fea_runner.run_fea_subprocess",
            side_effect=AssertionError("report generation must never invoke the solver"),
        ):
            document = build_report_document("Normal", project_root / "fea_results")

        self.assertEqual(("coarse",), CANONICAL_FEA_MESH_LEVELS)
        self.assertEqual(
            "d871c798897415bf83a5c0f54d38848cec68e16c52f3eaa5b2246837ac4b7969",
            solver_request_hash(normal_inputs, CANONICAL_FEA_MESH_LEVELS),
        )
        self.assertEqual("Solved Stage 1 FEA", document.selected_report.provenance)
        self.assertIsNotNone(document.selected_report.fea_package)

    def test_report_cache_reads_never_recover_or_mutate_backup_states(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        source_package = project_root / "fea_results" / EXACT_PACKAGE_HASH
        states = (
            ("valid destination", True, True, "Solved Stage 1 FEA"),
            ("invalid destination", True, False, "Analytical load estimate"),
            ("missing destination", False, False, "Analytical load estimate"),
        )

        for label, destination_exists, destination_is_valid, provenance in states:
            with self.subTest(state=label), TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                destination = root / EXACT_PACKAGE_HASH
                backup = root / f".{EXACT_PACKAGE_HASH}.backup"
                if destination_exists and destination_is_valid:
                    shutil.copytree(source_package, destination)
                elif destination_exists:
                    destination.mkdir()
                    (destination / "summary.json").write_bytes(b"not valid package JSON")
                shutil.copytree(source_package, backup)
                before = snapshot_tree(root)

                with patch(
                    "cyclewash_fea_runner.run_fea_subprocess",
                    side_effect=AssertionError("report generation must never invoke the solver"),
                ):
                    document = build_report_document("Normal", root)

                self.assertEqual(provenance, document.selected_report.provenance)
                self.assertEqual(before, snapshot_tree(root))

    def test_drivetrain_distinguishes_target_from_practical_drum_speed(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            document = build_report_document("Normal", Path(temporary_directory))
        formulas = {formula.identifier: formula for formula in document.formulas}
        drivetrain = formulas["drivetrain_speed_ratio"]
        angular_speed = formulas["angular_speed_and_edge_velocity"]

        self.assertIn(r"N_{d,practical}", drivetrain.latex)
        self.assertIn(r"N_{d,target}", drivetrain.latex)
        self.assertEqual(
            {"N_d,practical", "N_d,target", "N_p", "T_f", "T_r", "T_r,ideal"},
            {symbol.symbol for symbol in drivetrain.symbols},
        )
        self.assertIn("N_d,practical = (60.0 RPM)", drivetrain.evaluated)
        self.assertIn("= 63.750 RPM", drivetrain.evaluated)
        self.assertIn("N_d,target = 60.000 RPM", drivetrain.evaluated)
        self.assertIn(
            "analytical scenario calculations use N_d,target",
            drivetrain.explanation,
        )
        self.assertIn(r"N_{d,target}", angular_speed.latex)
        self.assertEqual(
            {"omega", "N_d,target", "R", "v_edge"},
            {symbol.symbol for symbol in angular_speed.symbols},
        )

    def test_exact_cache_adds_fea_component_summaries_and_definitions(self) -> None:
        project_root = Path(__file__).resolve().parents[1]

        document = build_report_document("Normal", project_root / "fea_results")
        report = document.selected_report
        self.assertTrue(hasattr(report, "fea_components"))
        components = {component.name: component for component in report.fea_components}

        self.assertEqual({"shaft", "gear", "drum"}, set(components))
        self.assertAlmostEqual(31_088_114.87012583, components["shaft"].maximum_von_mises_pa)
        self.assertAlmostEqual(4.212652778345388e-05, components["shaft"].maximum_displacement_m)
        self.assertAlmostEqual(8.041658397249357, components["shaft"].minimum_factor_of_safety)
        self.assertEqual(226, components["shaft"].node_count)
        self.assertEqual(619, components["shaft"].element_count)
        self.assertIn("Scenario: Normal", report.fea_summary)
        self.assertIn("Provenance: Solved Stage 1 FEA", report.fea_summary)
        self.assertTrue(any("shaft" in line.lower() for line in report.fea_summary))
        self.assertTrue(any("31.088 MPa" in line for line in report.fea_summary))
        self.assertTrue(any("0.0421 mm" in line for line in report.fea_summary))

        formulas = {formula.identifier: formula for formula in document.formulas}
        fea_formula = formulas["fea_result_definitions"]
        self.assertEqual(
            {
                "sigma_vm,max,c",
                "sigma_vm,c,i",
                "u_max,c",
                "u_x,c,j",
                "u_y,c,j",
                "u_z,c,j",
                "FoS_min,c",
                "FoS_c,j",
                "n_node,c",
                "n_element,c",
                "mesh_level",
                "c",
                "i",
                "j",
            },
            {symbol.symbol for symbol in fea_formula.symbols},
        )
        self.assertIn("mesh_level = coarse", fea_formula.evaluated)
        self.assertTrue(
            fea_formula.evaluated.startswith(
                "scenario = Normal; provenance = Solved Stage 1 FEA;"
            )
        )
        self.assertIn("shaft: sigma_vm,max = 3.108811e+07 Pa (31.088 MPa)", fea_formula.evaluated)
        self.assertIn("u_max = 4.212653e-05 m (0.0421 mm)", fea_formula.evaluated)
        self.assertIn("FoS_min = 8.0417", fea_formula.evaluated)
        self.assertIn("n_node = 226 nodes; n_element = 619 elements", fea_formula.evaluated)
        self.assertIn("linear-static", fea_formula.explanation)
        self.assertIn("not transient structural analysis or CFD", fea_formula.explanation)
        self.assertIn("Normal", fea_formula.explanation)
        self.assertIn("Solved Stage 1 FEA", fea_formula.explanation)

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
        with TemporaryDirectory() as temporary_directory:
            document = build_report_document("Heavy", Path(temporary_directory))
        formulas = {formula.identifier: formula for formula in document.formulas}
        identifiers = set(formulas)

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

        self.assertEqual(
            EXPECTED_FORMULA_SYMBOLS,
            {
                identifier: {symbol.symbol for symbol in formula.symbols}
                for identifier, formula in formulas.items()
            },
        )

        results = document.selected_report.results
        inputs = results.inputs
        analytical = results.analytical
        self.assertIn(
            f"T_nom = ({document.selected_report.scenario.human_power_w:.3f} W) / "
            f"({analytical.angular_speed_rad_s:.4f} rad/s)",
            formulas["human_power_torque_and_chain_force"].evaluated,
        )
        water = formulas["water_volume_mass_and_weight"].evaluated
        self.assertIn("pi x (0.270 m)^2 x (0.562 m)", water)
        self.assertIn(
            f"V_retained = ({analytical.full_drum_volume_m3:.6f} m^3) x (0.450) x (1 - 0.450)",
            water,
        )
        self.assertIn(
            f"m_water = ({inputs.water_density_kg_m3:.1f} kg/m^3) x "
            f"({analytical.retained_water_volume_m3:.6f} m^3)",
            water,
        )
        pressure = formulas["water_pressure"].evaluated
        self.assertIn(
            f"p_h = ({inputs.water_density_kg_m3:.1f} kg/m^3) x "
            f"({inputs.gravity_m_s2:.5f} m/s^2) x ({analytical.maximum_water_depth_m:.4f} m) x "
            f"({inputs.slosh_amplification:.3f})",
            pressure,
        )
        self.assertIn(
            f"({analytical.angular_speed_rad_s:.4f} rad/s)^2 x ({inputs.drum_radius_m:.3f} m)^2",
            pressure,
        )
        imbalance = formulas["unbalanced_wet_laundry_load"].evaluated
        self.assertIn(
            f"theta(0.000 s) = ({analytical.angular_speed_rad_s:.4f} rad/s) x "
            "(0.000 s) + (0.000 rad) = 0.000 rad",
            imbalance,
        )
        shaft = formulas["shaft_bending_and_torsion"].evaluated
        self.assertIn(
            f"M_chain = ({analytical.chain_force_n:.4f} N) x "
            f"({inputs.chain_force_overhang_m:.3f} m)",
            shaft,
        )
        self.assertIn(
            f"sigma_b = 32 x ({results.total_moment_n_m:.4f} N m) / "
            f"(pi x ({inputs.shaft_diameter_m:.3f} m)^3)",
            shaft,
        )
        combined = formulas["combined_stress_and_factor_of_safety"].evaluated
        self.assertIn(
            f"FoS_y = ({inputs.shaft_material.yield_strength_pa:.3e} Pa) / "
            f"({results.von_mises_pa:.3e} Pa)",
            combined,
        )

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
