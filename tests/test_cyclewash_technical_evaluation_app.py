"""Streamlit contract tests for the CycleWash technical-evaluation page."""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PAGE_PATH = PROJECT_ROOT / "pages" / "3_Technical_Evaluation.py"


class CycleWashTechnicalEvaluationAppTests(unittest.TestCase):
    def test_page_module_exposes_streamlit_main_entrypoint(self) -> None:
        from cyclewash_technical_evaluation_app import main

        self.assertTrue(callable(main))

    def test_page_wrapper_delegates_to_the_technical_evaluation_app(self) -> None:
        source = PAGE_PATH.read_text(encoding="utf-8")

        self.assertIn("from cyclewash_technical_evaluation_app import main", source)
        self.assertIn("main()", source)

    def test_page_source_declares_fixed_scenarios_continuous_viewer_and_exports(self) -> None:
        from cyclewash_scenarios import SCENARIOS
        from cyclewash_technical_evaluation_app import main

        source = Path(main.__code__.co_filename).read_text(encoding="utf-8")
        self.assertEqual(("Gentle", "Normal", "Heavy"), tuple(scenario.name for scenario in SCENARIOS))
        for required_text in (
            "CycleWash Technical Evaluation",
            "build_scenario_viewer_html",
            "Download PDF Report",
            "Download Offline HTML",
            "st.latex",
            "Core Engineering Checks",
            "Scenario Comparison",
            "core_formulas",
            "LIMITATIONS_NOTE",
        ):
            with self.subTest(required_text=required_text):
                self.assertIn(required_text, source)
        self.assertIn('PROJECT_ROOT / "fea_results"', source)

    def test_viewer_precedes_metrics_and_uses_asset_versioned_cache(self) -> None:
        from cyclewash_technical_evaluation_app import main

        source = Path(main.__code__.co_filename).read_text(encoding="utf-8")
        main_body = source[source.index("def main()") :]

        self.assertLess(
            main_body.index("st.iframe("),
            main_body.index("_render_selected_metrics(document)"),
        )
        self.assertIn("viewer_asset_fingerprint()", main_body)
        self.assertIn('width="stretch"', main_body)

    def test_viewer_cache_function_accepts_asset_fingerprint(self) -> None:
        import inspect
        from cyclewash_technical_evaluation_app import _cached_viewer_html

        parameter_names = tuple(inspect.signature(_cached_viewer_html).parameters)
        self.assertEqual(
            ("selected_name", "fea_root", "stl_root", "asset_fingerprint"),
            parameter_names,
        )

    def test_comparison_rows_contain_six_engineering_values(self) -> None:
        from cyclewash_technical_evaluation_app import _scenario_comparison_rows
        from cyclewash_technical_report import build_report_document

        document = build_report_document("Normal", PROJECT_ROOT / "fea_results")
        rows = _scenario_comparison_rows(document)

        self.assertEqual(3, len(rows))
        self.assertTrue(all(len(row) == 6 for row in rows))
        normal_row = next(row for row in rows if row[0] == "Normal")
        self.assertNotIn("Solved Stage 1 FEA", normal_row)

    def test_compound_latex_is_split_into_phone_width_displays(self) -> None:
        from cyclewash_technical_evaluation_app import _split_latex_displays
        from cyclewash_technical_report import build_report_document

        document = build_report_document("Normal", PROJECT_ROOT / "fea_results")
        shaft_formula = next(
            formula
            for formula in document.formulas
            if formula.identifier == "shaft_bending_and_torsion"
        )
        shaft_displays = _split_latex_displays(shaft_formula.latex)

        self.assertGreater(len(shaft_displays), 1)
        for formula in document.formulas:
            with self.subTest(formula=formula.identifier):
                displays = _split_latex_displays(formula.latex)
                self.assertTrue(displays)
                self.assertTrue(all(r"\qquad" not in display for display in displays))
                self.assertTrue(all(r"\quad" not in display for display in displays))
                self.assertLessEqual(max(map(len, displays)), 80)

    def test_all_three_page_entrypoints_run_without_exceptions(self) -> None:
        from streamlit.testing.v1 import AppTest

        entrypoints = (
            PROJECT_ROOT / "Gear_Builder.py",
            PROJECT_ROOT / "pages" / "2_Structural_Load_Visualizer.py",
            PAGE_PATH,
        )
        for entrypoint in entrypoints:
            with self.subTest(entrypoint=entrypoint.name):
                app = AppTest.from_file(str(entrypoint)).run(timeout=90)
                self.assertEqual([], app.exception)

    def test_fixed_scenario_selections_and_continuous_page_content(self) -> None:
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(str(PAGE_PATH)).run(timeout=60)

        self.assertEqual(("Gentle", "Normal", "Heavy"), tuple(app.button_group[0].options))
        self.assertEqual([], app.tabs)
        self.assertEqual(5, len(app.metric))
        self.assertEqual(
            ["Core Engineering Checks", "Scenario Comparison"],
            [header.value for header in app.header],
        )
        self.assertEqual(
            1,
            sum("not validated structural FEA or CFD" in item.value for item in app.info),
        )
        for removed in (
            "Exact Cached FEA",
            "Project Dimensions And Drivetrain",
            "Assumptions",
            "Provenance",
        ):
            self.assertNotIn(removed, " ".join(item.value for item in app.markdown))

        expected_rpm = {"Gentle": "45 RPM", "Normal": "60 RPM", "Heavy": "50 RPM"}
        for scenario_name in ("Gentle", "Normal", "Heavy"):
            with self.subTest(scenario=scenario_name):
                app.button_group[0].set_value(scenario_name).run(timeout=90)
                self.assertEqual([], app.exception)
                self.assertEqual(scenario_name, app.button_group[0].value)
                metrics = {metric.label: metric.value for metric in app.metric}
                self.assertEqual(expected_rpm[scenario_name], metrics["Current RPM"])
                self.assertEqual(4, len(app.subheader))
                self.assertGreaterEqual(len(app.download_button), 2)

    def test_report_load_failure_is_actionable_and_hides_the_exception(self) -> None:
        from streamlit.testing.v1 import AppTest
        import cyclewash_technical_evaluation_app as app_module

        with patch.object(
            app_module,
            "_cached_report_document",
            side_effect=ValueError("internal cache detail"),
        ):
            app = AppTest.from_file(str(PAGE_PATH)).run(timeout=60)

        self.assertEqual([], app.exception)
        self.assertEqual(1, len(app.error))
        self.assertEqual(
            "Technical evaluation could not load its local report or STL assets. "
            "Confirm the CycleWash project files are complete, then reload this page.",
            app.error[0].value,
        )
        self.assertNotIn("internal cache detail", app.error[0].value)


if __name__ == "__main__":
    unittest.main()
