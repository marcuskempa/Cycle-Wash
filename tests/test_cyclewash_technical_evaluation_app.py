"""Streamlit contract tests for the CycleWash technical-evaluation page."""

from __future__ import annotations

from pathlib import Path
import unittest


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

    def test_page_source_declares_fixed_scenarios_viewer_report_and_exports(self) -> None:
        from cyclewash_scenarios import SCENARIOS
        from cyclewash_technical_evaluation_app import main

        source = Path(main.__code__.co_filename).read_text(encoding="utf-8")
        self.assertEqual(("Gentle", "Normal", "Heavy"), tuple(scenario.name for scenario in SCENARIOS))
        for required_text in (
            "CycleWash Technical Evaluation",
            "Presentation",
            "Technical Report",
            "build_scenario_viewer_html",
            "Download PDF Report",
            "Download Offline HTML",
            "st.latex",
            "report.provenance",
        ):
            with self.subTest(required_text=required_text):
                self.assertIn(required_text, source)
        self.assertIn('PROJECT_ROOT / "fea_results"', source)

    def test_page_runs_without_streamlit_exceptions(self) -> None:
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(str(PAGE_PATH)).run(timeout=60)

        self.assertEqual([], app.exception)
        self.assertEqual("CycleWash Technical Evaluation", app.title[0].value)
        self.assertEqual(("Gentle", "Normal", "Heavy"), tuple(app.button_group[0].options))
        self.assertEqual("Normal", app.button_group[0].value)
        self.assertEqual(["Presentation", "Technical Report"], [tab.label for tab in app.tabs])
        self.assertGreaterEqual(len(app.latex), 8)
        self.assertGreaterEqual(len(app.download_button), 2)
        captions = [caption.value for caption in app.caption]
        self.assertIn("Analytical result provenance: Analytical load estimate.", captions)
        self.assertIn("Cached structural result provenance: Solved Stage 1 FEA.", captions)


if __name__ == "__main__":
    unittest.main()
