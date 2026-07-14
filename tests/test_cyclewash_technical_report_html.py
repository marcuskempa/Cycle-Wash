"""Contract tests for the standalone CycleWash technical-report HTML export."""

from __future__ import annotations

from dataclasses import replace
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import threading
import unittest

import numpy as np

from cyclewash_structural_visualizer import AssemblyPart
from cyclewash_technical_report import build_report_document


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _find_supported_browser() -> Path | None:
    candidates = [
        shutil.which(command)
        for command in ("chrome.exe", "chrome", "msedge.exe", "msedge")
    ]
    roots = tuple(
        root
        for root in (
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        )
        if root
    )
    for relative in (
        "Google/Chrome/Application/chrome.exe",
        "Microsoft/Edge/Application/msedge.exe",
    ):
        candidates.extend(str(Path(root) / relative) for root in roots)
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


class _RecordingHttpHandler(SimpleHTTPRequestHandler):
    request_paths: list[str] = []

    def do_GET(self) -> None:
        self.request_paths.append(self.path)
        super().do_GET()

    def log_message(self, format: str, *args: object) -> None:
        pass


class CycleWashTechnicalReportHtmlTests(unittest.TestCase):
    def setUp(self) -> None:
        from cyclewash_technical_report_html import build_offline_report_html

        self.document = build_report_document("Normal", PROJECT_ROOT / "fea_results")
        self.html_bytes = build_offline_report_html(self.document, PROJECT_ROOT)
        self.html = self.html_bytes.decode("utf-8")

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

        bundle = (PROJECT_ROOT / "assets" / "cyclewash-three-bundle.min.js").read_bytes()
        html_without_bundle = self.html_bytes.replace(bundle, b"", 1).lower()
        forbidden = (b"http://", b"https://", b"<script src=", b"<link href=", b"import(")
        self.assertTrue(all(token not in html_without_bundle for token in forbidden))
        self.assertEqual(
            self.html_bytes,
            build_offline_report_html(self.document, PROJECT_ROOT),
        )

    def test_pinned_bundle_is_unchanged_and_runtime_uses_its_actual_contract(self) -> None:
        bundle = (PROJECT_ROOT / "assets" / "cyclewash-three-bundle.min.js").read_bytes()

        self.assertIn(bundle, self.html_bytes)
        self.assertIn(b"http://www.w3.org/1999/xhtml", self.html_bytes)
        self.assertIn("window.CycleWashThree = CycleWashThree;", self.html)
        self.assertIn("const runtime = window.CycleWashThree;", self.html)
        self.assertIn("const THREE = runtime.THREE;", self.html)
        self.assertLess(
            self.html.index("const THREE = runtime.THREE;"),
            self.html.index("new THREE.WebGLRenderer"),
        )

    def test_runtime_smoke_contract_exercises_controls_and_reports_startup_errors(self) -> None:
        expected_runtime_contract = (
            "function runRuntimeSmokeTest()",
            'button.dataset.scenario === "Heavy"',
            "heavyButton.click();",
            'phaseSlider.dispatchEvent(new Event("input"));',
            'speedSelect.dispatchEvent(new Event("change"));',
            'body.dataset.viewerSmoke = "passed";',
            "smokeProbe.recordFrame();",
            "console.error(error);",
            'body.dataset.viewerSmoke = "failed";',
            'window.addEventListener("error"',
            'window.addEventListener("unhandledrejection"',
            "window.fetch = function",
            "window.XMLHttpRequest.prototype.open = function",
            "new PerformanceObserver",
        )

        for statement in expected_runtime_contract:
            self.assertIn(statement, self.html)
        self.assertLess(
            self.html.index("window.CycleWashRuntimeProbe = Object.freeze"),
            self.html.index("window.CycleWashThree = CycleWashThree;"),
        )

    def test_generated_page_executes_two_frames_and_makes_no_network_requests(self) -> None:
        browser = _find_supported_browser()
        if browser is None:
            self.skipTest(
                "runtime smoke requires Chrome or Edge in PATH or a common Windows install path"
            )

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "report.html").write_bytes(self.html_bytes)
            _RecordingHttpHandler.request_paths = []
            handler = partial(_RecordingHttpHandler, directory=str(root))
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            port = server.server_address[1]
            try:
                completed = subprocess.run(
                    [
                        str(browser),
                        "--headless=new",
                        "--disable-background-networking",
                        "--disable-component-update",
                        "--disable-default-apps",
                        "--disable-extensions",
                        "--disable-gpu-sandbox",
                        "--disable-sync",
                        "--no-default-browser-check",
                        "--no-first-run",
                        "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
                        "--run-all-compositor-stages-before-draw",
                        "--window-size=1280,900",
                        "--virtual-time-budget=5000",
                        f"--user-data-dir={root / 'browser-profile'}",
                        "--dump-dom",
                        f"http://127.0.0.1:{port}/report.html?cyclewash-smoke=1",
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    check=False,
                )
            finally:
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=5)

        self.assertEqual(0, completed.returncode, completed.stderr[-4000:])
        expected_markers = (
            'data-viewer-smoke="passed"',
            'data-viewer-smoke-frames="2"',
            'data-viewer-smoke-scenario="Heavy"',
            'data-viewer-smoke-playing="false"',
            'data-viewer-smoke-phase="123"',
            'data-viewer-smoke-speed="2"',
            'data-viewer-smoke-failures="0"',
            'data-viewer-smoke-network="0"',
        )
        for marker in expected_markers:
            self.assertIn(marker, completed.stdout)
        self.assertEqual(
            ["/report.html?cyclewash-smoke=1"], _RecordingHttpHandler.request_paths
        )

    def test_imbalance_arrow_uses_one_rotating_radial_reference_frame(self) -> None:
        self.assertIn(
            "const radialDirection = new THREE.Vector3(0, 1, 0);", self.html
        )
        self.assertIn("arrow.setDirection(radialDirection);", self.html)
        self.assertNotIn(
            "arrow.setDirection(new THREE.Vector3(0, Math.cos(radians), Math.sin(radians))",
            self.html,
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

    def test_geometry_budget_rejects_excessive_triangles_and_typed_array_bytes(self) -> None:
        from cyclewash_technical_report_html import (
            MAX_GEOMETRY_BYTES,
            MAX_TOTAL_TRIANGLES,
            _validate_geometry_budget,
        )

        vertices = np.asarray(
            [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            dtype=float,
        )
        too_many_faces = AssemblyPart(
            name="oversized triangles",
            local_vertices=vertices,
            faces=np.zeros((MAX_TOTAL_TRIANGLES + 1, 3), dtype=np.int64),
        )
        with self.assertRaisesRegex(ValueError, "triangle budget"):
            _validate_geometry_budget((too_many_faces,))

        oversized_vertices = np.zeros(
            ((MAX_GEOMETRY_BYTES // (3 * 4)) + 1, 3), dtype=float
        )
        too_many_bytes = AssemblyPart(
            name="oversized typed arrays",
            local_vertices=oversized_vertices,
            faces=np.asarray([[0, 0, 0]], dtype=np.int64),
        )
        with self.assertRaisesRegex(ValueError, "geometry byte budget"):
            _validate_geometry_budget((too_many_bytes,))


if __name__ == "__main__":
    unittest.main()
