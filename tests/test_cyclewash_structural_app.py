"""Structural-page contracts for FEA availability and hosted preview behavior."""

from __future__ import annotations

from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PAGE_PATH = PROJECT_ROOT / "pages" / "2_Structural_Load_Visualizer.py"


class FeaActionStateTests(unittest.TestCase):
    def test_exact_cache_has_load_action(self) -> None:
        from cyclewash_structural_app import resolve_fea_action_state

        state = resolve_fea_action_state(
            cached_package_available=True,
            solver_available=False,
        )

        self.assertEqual("cache", state.mode)
        self.assertEqual("Load Cached Stage 1 FEA", state.action_label)

    def test_exact_cache_wins_when_local_solver_is_also_available(self) -> None:
        from cyclewash_structural_app import resolve_fea_action_state

        state = resolve_fea_action_state(
            cached_package_available=True,
            solver_available=True,
        )

        self.assertEqual("cache", state.mode)
        self.assertEqual("Load Cached Stage 1 FEA", state.action_label)

    def test_local_solver_has_run_action(self) -> None:
        from cyclewash_structural_app import resolve_fea_action_state

        state = resolve_fea_action_state(
            cached_package_available=False,
            solver_available=True,
        )

        self.assertEqual("solve", state.mode)
        self.assertEqual("Run Stage 1 FEA", state.action_label)

    def test_hosted_state_is_automatic_analytical_preview(self) -> None:
        from cyclewash_structural_app import resolve_fea_action_state

        state = resolve_fea_action_state(
            cached_package_available=False,
            solver_available=False,
        )

        self.assertEqual("analytical", state.mode)
        self.assertIsNone(state.action_label)
        self.assertIn("Analytical preview", state.notice)
        self.assertIn("run locally", state.notice.lower())

    def test_nondefault_fill_and_relief_show_preview_without_disabled_fea_action(self) -> None:
        from cyclewash_fea_runner import FeaSolverStatus
        import cyclewash_structural_app as app_module
        from streamlit.testing.v1 import AppTest

        unavailable = FeaSolverStatus(
            available=False,
            python_path=None,
            versions={},
            message="Stage 1 FEA solver is not installed.",
        )
        with patch.object(app_module, "detect_fea_solver", return_value=unavailable):
            app = AppTest.from_file(str(PAGE_PATH)).run(timeout=90)
            app.button_group[0].set_value("Simplified Stage 1 FEA").run(timeout=90)
            next(item for item in app.slider if item.label == "Drum fill (%)").set_value(47).run(timeout=90)
            next(item for item in app.slider if item.label == "Perforation relief (%)").set_value(38).run(timeout=90)

        self.assertEqual([], app.exception)
        self.assertIn("Analytical preview", [item.value for item in app.subheader])
        self.assertFalse(any(item.label == "Run Stage 1 FEA" for item in app.button))
        self.assertTrue(any("run locally" in item.value.lower() for item in app.info))
        summary = "\n".join(item.value for item in app.code)
        self.assertIn("m_water", summary)
        self.assertIn("p_design", summary)
        self.assertIn("47%", summary)
        self.assertIn("38%", summary)

    def test_hosted_preview_with_missing_stl_geometry_stays_actionable(self) -> None:
        from cyclewash_fea_runner import FeaSolverStatus
        import cyclewash_structural_app as app_module
        from streamlit.testing.v1 import AppTest

        unavailable = FeaSolverStatus(
            available=False,
            python_path=None,
            versions={},
            message="Stage 1 FEA solver is not installed.",
        )
        with TemporaryDirectory() as empty_directory:
            with patch.object(app_module, "detect_fea_solver", return_value=unavailable):
                app = AppTest.from_file(str(PAGE_PATH)).run(timeout=90)
                app.button_group[0].set_value("Simplified Stage 1 FEA").run(timeout=90)
                next(
                    item
                    for item in app.text_input
                    if item.label == "Animation STL folder or file"
                ).set_value(empty_directory).run(timeout=90)
                next(
                    item for item in app.slider if item.label == "Drum fill (%)"
                ).set_value(47).run(timeout=90)

        self.assertEqual([], app.exception)
        messages = [item.value for item in (*app.warning, *app.error, *app.info)]
        self.assertTrue(
            any("analytical preview" in message.lower() for message in messages)
        )
        self.assertTrue(any("shaft" in message.lower() for message in messages))

    def test_hosted_preview_with_incomplete_stl_geometry_lists_missing_parts(self) -> None:
        from cyclewash_fea_runner import FeaSolverStatus
        import cyclewash_structural_app as app_module
        from streamlit.testing.v1 import AppTest

        unavailable = FeaSolverStatus(
            available=False,
            python_path=None,
            versions={},
            message="Stage 1 FEA solver is not installed.",
        )
        with TemporaryDirectory() as incomplete_directory:
            shutil.copyfile(
                PROJECT_ROOT / "shaft.stl",
                Path(incomplete_directory) / "shaft.stl",
            )
            with patch.object(
                app_module, "detect_fea_solver", return_value=unavailable
            ):
                app = AppTest.from_file(str(PAGE_PATH)).run(timeout=90)
                app.button_group[0].set_value("Simplified Stage 1 FEA").run(timeout=90)
                next(
                    item
                    for item in app.text_input
                    if item.label == "Animation STL folder or file"
                ).set_value(incomplete_directory).run(timeout=90)
                next(
                    item for item in app.slider if item.label == "Drum fill (%)"
                ).set_value(47).run(timeout=90)

        self.assertEqual([], app.exception)
        messages = [item.value for item in (*app.warning, *app.error, *app.info)]
        combined = "\n".join(messages).lower()
        self.assertIn("analytical preview", combined)
        self.assertIn("gear", combined)
        self.assertIn("inner drum", combined)

    def test_unavailable_solver_displays_its_specific_diagnostic(self) -> None:
        from cyclewash_fea_runner import FeaSolverStatus
        import cyclewash_structural_app as app_module
        from streamlit.testing.v1 import AppTest

        diagnostic = (
            "Stage 1 FEA solver dependencies are unavailable or broken. "
            "Re-run setup_cyclewash_fea.bat."
        )
        broken = FeaSolverStatus(
            available=False,
            python_path=Path("work/.fea-venv/Scripts/python.exe"),
            versions={},
            message=diagnostic,
        )
        with patch.object(app_module, "detect_fea_solver", return_value=broken):
            app = AppTest.from_file(str(PAGE_PATH)).run(timeout=90)
            app.button_group[0].set_value("Simplified Stage 1 FEA").run(timeout=90)

        self.assertEqual([], app.exception)
        visible_messages = [
            item.value
            for collection in (app.caption, app.info, app.warning, app.error)
            for item in collection
        ]
        self.assertIn(diagnostic, visible_messages)

    def test_invalid_request_path_cache_falls_back_without_solved_provenance(self) -> None:
        from cyclewash_fea_runner import FeaSolverStatus
        import cyclewash_structural_app as app_module
        from streamlit.testing.v1 import AppTest

        cases = (
            (True, "Run Stage 1 FEA", False),
            (False, None, True),
        )
        with TemporaryDirectory() as temporary_directory:
            candidate_path = Path(temporary_directory)
            (candidate_path / "summary.json").write_text("{}", encoding="utf-8")

            for solver_available, expected_action, expect_preview in cases:
                with self.subTest(solver_available=solver_available):
                    status = FeaSolverStatus(
                        available=solver_available,
                        python_path=(Path("solver-python") if solver_available else None),
                        versions=({"sfepy": "test", "gmsh": "test"} if solver_available else {}),
                        message="Stage 1 FEA solver status for test.",
                    )
                    with (
                        patch.object(app_module, "detect_fea_solver", return_value=status),
                        patch.object(app_module, "fea_package_path", return_value=candidate_path),
                        patch.object(
                            app_module,
                            "load_stage1_package",
                            side_effect=ValueError("request-path cache is invalid"),
                        ),
                    ):
                        app = AppTest.from_file(str(PAGE_PATH)).run(timeout=90)
                        app.button_group[0].set_value("Simplified Stage 1 FEA").run(timeout=90)

                    self.assertEqual([], app.exception)
                    self.assertTrue(
                        any("rejected" in item.value.lower() for item in app.warning)
                    )
                    action_labels = [item.label for item in app.button]
                    self.assertNotIn("Load Cached Stage 1 FEA", action_labels)
                    if expected_action is not None:
                        self.assertIn(expected_action, action_labels)
                    self.assertEqual(
                        expect_preview,
                        "Analytical preview" in [item.value for item in app.subheader],
                    )
                    notices = [
                        item.value
                        for collection in (app.caption, app.info, app.warning)
                        for item in collection
                    ]
                    self.assertFalse(
                        any("exact solved" in notice.lower() for notice in notices)
                    )


class AnalyticalPreviewGeometryTests(unittest.TestCase):
    def test_preview_closes_door_without_moving_enclosure_or_changing_opacity(self) -> None:
        from cyclewash_engineering_model import (
            EngineeringInputs,
            calculate_engineering_loads,
        )
        from cyclewash_structural_app import build_stage1_analytical_preview
        from cyclewash_structural_visualizer import AssemblyPart, Transform

        faces = np.asarray(
            [[0, 1, 2], [0, 3, 1], [0, 2, 3], [1, 3, 2]], dtype=np.int64
        )
        door_vertices = np.asarray(
            [
                [0.0, 0.0, 0.0],
                [0.0, 2.0, 0.0],
                [0.0, 0.0, 2.0],
                [2.0, 0.0, 0.0],
            ],
            dtype=float,
        )
        enclosure_vertices = door_vertices + np.asarray([10.0, 20.0, 30.0])
        parts = [
            AssemblyPart(name="door", local_vertices=door_vertices, faces=faces),
            AssemblyPart(
                name="enclosure", local_vertices=enclosure_vertices, faces=faces
            ),
        ]
        inputs = EngineeringInputs()

        figure, _ = build_stage1_analytical_preview(
            parts,
            inputs,
            calculate_engineering_loads(inputs),
            colorscale="Turbo",
            phase_degrees=0.0,
        )

        traces = {trace.name: trace for trace in figure.data}
        observed_door = np.column_stack(
            (traces["door"].x, traces["door"].y, traces["door"].z)
        )
        hinge_origin = np.asarray([0.0, 2.0 / 3.0, 2.0 / 3.0])
        expected_door = Transform.from_rotation(
            (0.0, 0.0, -1.0), 90.0, origin=hinge_origin
        ).apply(door_vertices)

        np.testing.assert_allclose(observed_door, expected_door, atol=1.0e-12)
        np.testing.assert_allclose(
            np.column_stack(
                (
                    traces["enclosure"].x,
                    traces["enclosure"].y,
                    traces["enclosure"].z,
                )
            ),
            enclosure_vertices,
            atol=1.0e-12,
        )
        self.assertEqual(0.5, traces["door"].opacity)
        self.assertEqual(0.5, traces["enclosure"].opacity)
