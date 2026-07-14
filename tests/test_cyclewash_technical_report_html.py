"""Contract tests for the standalone CycleWash technical-report HTML export."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cyclewash_technical_report import build_report_document


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CycleWashTechnicalReportHtmlTests(unittest.TestCase):
    def setUp(self) -> None:
        from cyclewash_technical_report_html import build_offline_report_html

        self.document = build_report_document("Normal", PROJECT_ROOT / "fea_results")
        self.html = build_offline_report_html(self.document, PROJECT_ROOT).decode("utf-8")

    def test_export_embeds_report_content_three_bundle_and_required_controls(self) -> None:
        self.assertTrue(self.html.startswith("<!doctype html>"))
        self.assertIn("CycleWash Technical Evaluation", self.html)
        self.assertIn("OrbitControls", self.html)
        self.assertIn("Relative analytical load", self.html)
        self.assertIn("Scenario comparison", self.html)
        self.assertIn("Assumptions", self.html)
        self.assertIn("Limitations", self.html)
        self.assertIn("Engineering interpretation", self.html)
        self.assertIn("Conclusion", self.html)
        for scenario in ("Gentle", "Normal", "Heavy"):
            self.assertIn(scenario, self.html)
        for control in ("scenario-selector", "play-pause", "phase-slider", "speed-select"):
            self.assertIn(f'id="{control}"', self.html)
        for formula in self.document.formulas:
            self.assertIn(formula.title, self.html)
            self.assertIn(formula.evaluated, self.html)
            self.assertIn("Symbol", self.html)
            self.assertIn("Source", self.html)

    def test_export_is_offline_safe_and_deterministic(self) -> None:
        from cyclewash_technical_report_html import build_offline_report_html

        forbidden = ("http://", "https://", "<script src=", "<link href=", "import(")
        self.assertTrue(all(token not in self.html.lower() for token in forbidden))
        self.assertEqual(
            self.html.encode("utf-8"),
            build_offline_report_html(self.document, PROJECT_ROOT),
        )

    def test_geometry_is_embedded_once_and_shared_across_scenarios(self) -> None:
        payload_text = self.html.split(
            '<script id="cyclewash-report-data" type="application/json">', 1
        )[1].split("</script>", 1)[0]
        payload = json.loads(payload_text)

        self.assertEqual({"Gentle", "Normal", "Heavy"}, set(payload["scenarios"]))
        self.assertIn("parts", payload["geometry"])
        self.assertGreaterEqual(len(payload["geometry"]["parts"]), 5)
        self.assertNotIn("geometry", payload["scenarios"]["Gentle"])
        self.assertEqual(1, payload_text.count('"geometry":{"parts"'))
        for part in payload["geometry"]["parts"]:
            self.assertIn("base64", part["geometry"]["positions"])
            self.assertIn("base64", part["geometry"]["indices"])

    def test_scenario_viewer_has_fixed_selected_scenario_and_metrics(self) -> None:
        from cyclewash_technical_report_html import build_scenario_viewer_html

        viewer = build_scenario_viewer_html(self.document, "Heavy", PROJECT_ROOT)

        self.assertIn('data-selected-scenario="Heavy"', viewer)
        self.assertIn("Wet laundry", viewer)
        self.assertIn("Imbalance force", viewer)
        self.assertIn("water-body", viewer)
        self.assertIn("imbalance-vector", viewer)

    def test_missing_and_malformed_stl_inputs_raise_actionable_errors(self) -> None:
        from cyclewash_technical_report_html import build_offline_report_html

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with self.assertRaisesRegex(ValueError, "missing required STL.*enclosure"):
                build_offline_report_html(self.document, root)

            for filename in ("enclosure.stl", "door.stl", "Inner_Drum.stl", "gear.stl", "shaft.stl"):
                (root / filename).write_text("not an STL", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unable to load STL.*enclosure"):
                build_offline_report_html(self.document, root)

    def test_payload_script_escapes_user_text_that_could_close_a_script_tag(self) -> None:
        from cyclewash_technical_report_html import build_offline_report_html

        hostile_document = replace(
            self.document,
            conclusion="Conclusion </script><script>window.evil = true</script>",
        )
        html = build_offline_report_html(hostile_document, PROJECT_ROOT).decode("utf-8")

        self.assertNotIn("</script><script>window.evil", html)
        self.assertIn("\\u003c/script\\u003e", html)


if __name__ == "__main__":
    unittest.main()
