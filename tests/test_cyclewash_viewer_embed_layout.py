"""Regression checks for the compact Streamlit viewer embed."""

from pathlib import Path
import unittest

from cyclewash_technical_report import build_report_document
from cyclewash_technical_report_html import build_scenario_viewer_html
from cyclewash_technical_evaluation_app import _format_stress_mpa


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ViewerEmbedLayoutTests(unittest.TestCase):
    def test_metric_stress_can_use_compact_precision(self) -> None:
        self.assertEqual("43.3 MPa", _format_stress_mpa(43.30e6, digits=1))

    def test_viewer_only_mode_fills_the_iframe_without_page_overflow(self) -> None:
        document = build_report_document("Normal", PROJECT_ROOT / "fea_results")
        viewer = build_scenario_viewer_html(document, "Normal", PROJECT_ROOT)

        self.assertIn(
            'body[data-viewer-only="true"] { overflow: hidden;',
            viewer,
        )
        self.assertIn(
            'body[data-viewer-only="true"] .viewer-grid { height: 100vh;',
            viewer,
        )
        self.assertIn(
            'body[data-viewer-only="true"] .scene-wrap,',
            viewer,
        )


if __name__ == "__main__":
    unittest.main()
