"""Structural-page contracts for FEA availability and hosted preview behavior."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


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
