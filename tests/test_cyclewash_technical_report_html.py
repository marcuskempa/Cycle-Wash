"""Contract tests for the standalone CycleWash technical-report HTML export."""

from __future__ import annotations

import base64
from dataclasses import replace
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

import numpy as np

from cyclewash_structural_visualizer import AssemblyPart
from cyclewash_technical_report import (
    CORE_FORMULA_IDS,
    LIMITATIONS_NOTE,
    build_report_document,
    core_formulas,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _payload_from_html(html: str) -> dict[str, object]:
    payload_text = html.split(
        '<script id="cyclewash-report-data" type="application/json">', 1
    )[1].split("</script>", 1)[0]
    return json.loads(payload_text)


def _without_drum_envelope(html: str) -> str:
    prefix = '<script id="cyclewash-report-data" type="application/json">'
    before, payload_and_after = html.split(prefix, 1)
    payload_text, after = payload_and_after.split("</script>", 1)
    payload = json.loads(payload_text)
    payload["geometry"].pop("drum_envelope", None)
    stale_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"{before}{prefix}{stale_payload}</script>{after}"


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
        self.assertIn("Limitations", self.html)
        self.assertIn("Conclusion", self.html)
        for scenario in ("Gentle", "Normal", "Heavy"):
            self.assertIn(scenario, self.html)
        for control in ("scenario-selector", "play-pause", "phase-slider", "speed-select"):
            self.assertIn(f'id="{control}"', self.html)
        for formula in core_formulas(self.document):
            self.assertIn(formula.title, self.html)
            self.assertIn(formula.evaluated, self.html)
            self.assertIn("Symbol", self.html)
            self.assertIn("Evaluated substitution", self.html)

    def test_embedded_viewer_only_exposes_animation_controls(self) -> None:
        from cyclewash_technical_report_html import build_scenario_viewer_html

        viewer = build_scenario_viewer_html(self.document, "Normal", PROJECT_ROOT)

        self.assertIn('data-viewer-only="true"', viewer)
        self.assertIn('body[data-viewer-only="true"] .viewer-header', viewer)
        self.assertIn('body[data-viewer-only="true"] .metrics', viewer)
        self.assertIn('body[data-viewer-only="true"] .scene-label', viewer)
        self.assertIn('id="play-pause"', viewer)
        self.assertIn('id="phase-slider"', viewer)
        self.assertIn('id="speed-select"', viewer)

    def test_viewer_only_uses_horizontal_toolbar_above_full_width_canvas(self) -> None:
        from cyclewash_technical_report_html import build_scenario_viewer_html

        viewer = build_scenario_viewer_html(self.document, "Normal", PROJECT_ROOT)

        self.assertLess(
            viewer.index('<div class="controls"'),
            viewer.index('<div class="scene-wrap">'),
        )
        self.assertIn('grid-template-areas: "playback phase speed";', viewer)
        self.assertNotIn("grid-template-columns: minmax(0, 1fr) 280px", viewer)
        self.assertIn(
            '<p id="viewer-status" class="viewer-status" role="status" '
            'aria-live="polite">Viewer starting...</p>',
            viewer,
        )
        self.assertIn("viewerStatus.hidden = true;", viewer)

    def test_viewer_asset_fingerprint_changes_with_template_or_runtime(self) -> None:
        import cyclewash_technical_report_html as html_module

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            template = root / "template.html"
            runtime = root / "runtime.js"
            template.write_text("template-v1", encoding="utf-8")
            runtime.write_bytes(b"runtime-v1")
            with patch.object(html_module, "TEMPLATE_PATH", template), patch.object(
                html_module, "THREE_BUNDLE_PATH", runtime
            ):
                first = html_module.viewer_asset_fingerprint()
                template.write_text("template-v2", encoding="utf-8")
                second = html_module.viewer_asset_fingerprint()
                runtime.write_bytes(b"runtime-v2")
                third = html_module.viewer_asset_fingerprint()
                with patch.object(
                    html_module,
                    "VIEWER_PAYLOAD_SCHEMA_VERSION",
                    "cyclewash-test-schema-v2",
                    create=True,
                ):
                    fourth = html_module.viewer_asset_fingerprint()

        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertNotEqual(first, second)
        self.assertNotEqual(second, third)
        self.assertNotEqual(third, fourth)

    def test_viewer_recovers_drum_envelope_from_stale_payload_geometry(self) -> None:
        stale_html = _without_drum_envelope(self.html)
        stale_payload = _payload_from_html(stale_html)
        expected_runtime_contract = (
            "function deriveDrumEnvelope(parts)",
            "function resolveDrumEnvelope(geometryPayload)",
            "geometryPayload.drum_envelope",
            'normalizedName === "inner drum"',
            "const drumEnvelope = resolveDrumEnvelope(payload.geometry);",
            "new THREE.Vector3().fromArray(drumEnvelope.center_m)",
            "new THREE.Vector3().fromArray(drumEnvelope.span_m)",
        )

        self.assertNotIn("drum_envelope", stale_payload["geometry"])
        for statement in expected_runtime_contract:
            self.assertIn(statement, stale_html)

    def test_offline_report_contains_only_core_equations_and_one_limitation(self) -> None:
        rendered_formula_ids = tuple(
            re.findall(r'<h3 id="formula-([^"]+)">', self.html)
        )
        self.assertEqual(CORE_FORMULA_IDS, rendered_formula_ids)
        self.assertNotIn("Human Power, Torque, And Chain Force", self.html)
        self.assertNotIn("Exact FEA Result And Provenance", self.html)
        self.assertNotIn("Project Dimensions And Drivetrain", self.html)
        self.assertEqual(1, self.html.count(LIMITATIONS_NOTE))

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

    def test_stale_payload_page_executes_two_frames_without_network_requests(self) -> None:
        browser = _find_supported_browser()
        if browser is None:
            self.skipTest(
                "runtime smoke requires Chrome or Edge in PATH or a common Windows install path"
            )

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            stale_html = _without_drum_envelope(self.html)
            (root / "report.html").write_text(stale_html, encoding="utf-8")
            _RecordingHttpHandler.request_paths = []
            handler = partial(_RecordingHttpHandler, directory=str(root))
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            port = server.server_address[1]
            try:
                browser_command = [
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
                ]
                try:
                    completed = subprocess.run(
                        browser_command,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=30,
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    self.skipTest(
                        "installed headless Chrome did not complete --dump-dom; "
                        "run the final browser smoke check instead"
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
        self.assertEqual(1, payload_text.count('"parts":['))
        for part in payload["geometry"]["parts"]:
            self.assertIn("base64", part["geometry"]["positions"])
            self.assertIn("base64", part["geometry"]["indices"])

    def test_payload_closes_door_without_moving_enclosure_or_changing_opacity(self) -> None:
        from cyclewash_geometry_policy import normalize_stl_part
        from cyclewash_structural_visualizer import StlPartSpec, Transform, load_stl_part

        payload = _payload_from_html(self.html)
        payload_parts = {
            part["name"]: part for part in payload["geometry"]["parts"]
        }

        def payload_vertices(name: str) -> np.ndarray:
            positions = payload_parts[name]["geometry"]["positions"]
            return np.frombuffer(
                base64.b64decode(positions["base64"]), dtype=np.float32
            ).reshape(positions["shape"])

        def normalized_source(name: str, filename: str) -> AssemblyPart:
            source = load_stl_part(
                StlPartSpec(name=name, source=PROJECT_ROOT / filename)
            )
            return normalize_stl_part(source).part

        open_door = normalized_source("door", "door.stl")
        door_vertices = np.asarray(open_door.vertices, dtype=float)
        minimum_x = float(door_vertices[:, 0].min())
        tolerance = max(float(np.ptp(door_vertices[:, 0])) * 1.0e-6, 1.0e-9)
        hinge_vertices = door_vertices[
            np.isclose(door_vertices[:, 0], minimum_x, atol=tolerance, rtol=0.0)
        ]
        hinge_origin = hinge_vertices.mean(axis=0)
        hinge_origin[0] = minimum_x
        expected_closed_door = Transform.from_rotation(
            (0.0, 0.0, -1.0), 90.0, origin=hinge_origin
        ).apply(door_vertices)

        np.testing.assert_allclose(
            payload_vertices("door"), expected_closed_door, rtol=1.0e-6, atol=1.0e-7
        )
        self.assertFalse(np.allclose(payload_vertices("door"), door_vertices))

        enclosure = normalized_source("enclosure", "enclosure.stl")
        np.testing.assert_allclose(
            payload_vertices("enclosure"),
            enclosure.vertices,
            rtol=1.0e-6,
            atol=1.0e-7,
        )
        self.assertEqual(0.5, payload_parts["door"]["opacity"])
        self.assertEqual(0.5, payload_parts["enclosure"]["opacity"])

    def test_payload_preserves_shaft_pivot_and_adds_drum_envelope(self) -> None:
        from cyclewash_technical_report_html import _load_normalized_parts

        payload = _payload_from_html(self.html)
        envelope = payload["geometry"]["drum_envelope"]
        shaft = next(
            part for part in _load_normalized_parts(PROJECT_ROOT) if part.name == "shaft"
        )
        shaft_vertices = np.asarray(shaft.vertices, dtype=float)
        expected_origin = (shaft_vertices.min(axis=0) + shaft_vertices.max(axis=0)) / 2.0

        np.testing.assert_allclose(payload["geometry"]["rotation_origin"], expected_origin)
        self.assertEqual(3, len(envelope["center_m"]))
        self.assertEqual(3, len(envelope["span_m"]))
        self.assertTrue(all(value > 0.0 for value in envelope["span_m"]))

    def test_viewer_uses_blender_z_up_and_drum_relative_contents(self) -> None:
        self.assertIn("camera.up.set(0, 0, 1)", self.html)
        self.assertIn("grid.rotation.x = Math.PI / 2", self.html)
        self.assertIn("drumEnvelope.center_m", self.html)
        self.assertIn("const laundryBase = drumCenter.clone().sub(origin)", self.html)
        self.assertIn("water.position.copy(drumCenter)", self.html)
        self.assertNotIn("new THREE.BoxGeometry(0.55, 0.16, 0.34)", self.html)

    def test_water_and_laundry_remain_inside_drum_envelope_at_cardinal_phases(
        self,
    ) -> None:
        payload = _payload_from_html(self.html)
        geometry = payload["geometry"]
        envelope = geometry["drum_envelope"]
        origin = np.asarray(geometry["rotation_origin"], dtype=float)
        drum_center = np.asarray(envelope["center_m"], dtype=float)
        drum_span = np.asarray(envelope["span_m"], dtype=float)
        lower_bound = drum_center - drum_span / 2.0
        upper_bound = drum_center + drum_span / 2.0
        laundry_radius_m = 0.035

        for scenario_name, scenario in payload["scenarios"].items():
            fill_fraction = float(scenario["fill_fraction"])
            eccentricity_m = float(scenario["eccentricity_m"])
            laundry_local = drum_center - origin + np.asarray(
                [0.0, eccentricity_m, 0.0]
            )
            water_radii = np.asarray(
                [
                    drum_span[0] * 0.38,
                    drum_span[1] * 0.38,
                    drum_span[2] * 0.38 * max(0.18, fill_fraction),
                ]
            )
            water_center = drum_center.copy()
            water_center[2] -= drum_span[2] * 0.18 * (1.0 - fill_fraction)

            for phase_degrees in (0.0, 90.0, 180.0, 270.0):
                phase_radians = np.deg2rad(phase_degrees)
                cosine = np.cos(phase_radians)
                sine = np.sin(phase_radians)
                laundry_center = origin + np.asarray(
                    [
                        laundry_local[0],
                        cosine * laundry_local[1] - sine * laundry_local[2],
                        sine * laundry_local[1] + cosine * laundry_local[2],
                    ]
                )

                slosh_radians = np.sin(
                    phase_radians - np.pi / 4.0
                ) * np.deg2rad(8.0)
                slosh_cosine = np.cos(slosh_radians)
                slosh_sine = np.sin(slosh_radians)
                water_extents = np.asarray(
                    [
                        water_radii[0],
                        np.hypot(
                            water_radii[1] * slosh_cosine,
                            water_radii[2] * slosh_sine,
                        ),
                        np.hypot(
                            water_radii[1] * slosh_sine,
                            water_radii[2] * slosh_cosine,
                        ),
                    ]
                )

                context = f"{scenario_name} at {phase_degrees:.0f} degrees"
                self.assertTrue(
                    np.all(laundry_center - laundry_radius_m >= lower_bound),
                    f"laundry lower extent escaped the drum for {context}",
                )
                self.assertTrue(
                    np.all(laundry_center + laundry_radius_m <= upper_bound),
                    f"laundry upper extent escaped the drum for {context}",
                )
                self.assertTrue(
                    np.all(water_center - water_extents >= lower_bound),
                    f"water lower extent escaped the drum for {context}",
                )
                self.assertTrue(
                    np.all(water_center + water_extents <= upper_bound),
                    f"water upper extent escaped the drum for {context}",
                )

    def test_scenario_viewer_has_fixed_selected_scenario_and_metrics(self) -> None:
        from cyclewash_technical_report_html import build_scenario_viewer_html

        viewer = build_scenario_viewer_html(self.document, "Heavy", PROJECT_ROOT)

        self.assertIn('data-selected-scenario="Heavy"', viewer)
        self.assertIn('data-viewer-only="true"', viewer)
        self.assertIn(
            'body[data-viewer-only="true"] .scenario-selector { display: none; }',
            viewer,
        )
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
