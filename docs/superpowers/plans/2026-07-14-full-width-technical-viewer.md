# CycleWash Full-Width Technical Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a reliable full-width Technical Evaluation viewer and make custom Structural Load Visualizer inputs produce an honest, automatic analytical preview when hosted FEA is unavailable.

**Architecture:** Keep Streamlit as the source of truth for Gentle, Normal, and Heavy scenarios while the embedded self-contained Three.js document owns only playback state. Version the generated viewer cache from the HTML template and bundled runtime, replace the iframe's split layout with a horizontal toolbar plus full-width canvas, and model FEA availability as three explicit UI states. Reuse the existing authoritative STL loader, engineering calculations, Plotly load map, report generators, and exact cached FEA package format.

**Tech Stack:** Python 3, Streamlit 1.59+, Plotly, NumPy, embedded Three.js 0.185.1, `unittest`, Streamlit `AppTest`, headless Chrome/Edge smoke tests.

## Global Constraints

- Preserve the approved Gentle, Normal, and Heavy scenario values, formulas, STL coordinate system, and analytical assumptions.
- Keep the viewer self-contained: no runtime JavaScript, texture, or STL network requests.
- Do not add a bidirectional Streamlit component or move scenario ownership into the iframe.
- Do not add new FEA or CFD capabilities and do not install Gmsh/SfePy in Streamlit Community Cloud.
- Preserve the existing two-page PDF and self-contained offline HTML exports.
- Label non-solved values `Analytical preview`; never call them solved FEA stress.
- Keep exactly one limitations note on the Technical Evaluation page and in each export.
- Deploy repository `marcuskempa/Cycle-Wash`, branch `main`, entrypoint `Gear_Builder.py`.

---

## File Ownership Map

- `cyclewash_technical_report_template.html`: iframe/standalone viewer markup, responsive layout, startup status, and Three.js runtime behavior.
- `cyclewash_technical_report_html.py`: deterministic viewer generation, embedded assets, normalized authoritative STL payload, and viewer asset fingerprint.
- `cyclewash_technical_evaluation_app.py`: Streamlit scenario state, page ordering, viewer cache key, metrics, calculations, comparison, limitations, and downloads.
- `cyclewash_structural_app.py`: Stage 1 cache/solver/analytical availability state and analytical Plotly preview rendering.
- `cyclewash_engineering_model.py`: copyable analytical summary text for current fill, relief, retained mass, and pressure.
- `tests/test_cyclewash_technical_report_html.py`: offline/runtime viewer contracts and browser smoke test.
- `tests/test_cyclewash_viewer_embed_layout.py`: compact iframe layout regression checks.
- `tests/test_cyclewash_technical_evaluation_app.py`: Streamlit page composition and scenario behavior.
- `tests/test_cyclewash_structural_app.py`: new focused structural availability and hosted-preview tests.
- `README.md`: local run and Streamlit Community Cloud deployment contract.

---

### Task 1: Self-Contained Full-Width Viewer And Asset Versioning

**Files:**
- Modify: `cyclewash_technical_report_html.py:1-80, 230-345`
- Modify: `cyclewash_technical_report_template.html:1-165, 345-385`
- Modify: `tests/test_cyclewash_technical_report_html.py:70-170`
- Modify: `tests/test_cyclewash_viewer_embed_layout.py:1-45`

**Interfaces:**
- Produces: `viewer_asset_fingerprint() -> str`, a deterministic SHA-256 hexadecimal digest of `TEMPLATE_PATH` and `THREE_BUNDLE_PATH` contents.
- Preserves: `build_scenario_viewer_html(document: ReportDocument, selected_name: str, stl_root: str | Path) -> str`.
- Preserves: `build_offline_report_html(document: ReportDocument, stl_root: str | Path) -> bytes`.
- Produces HTML with `.controls` before `.scene-wrap`, viewer-only grid areas `"playback phase speed"`, visible `Viewer starting...` status, and visible startup/runtime errors.

- [ ] **Step 1: Add failing layout, startup-status, and fingerprint tests**

Add these focused contracts to the existing test classes:

```python
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

    self.assertRegex(first, r"^[0-9a-f]{64}$")
    self.assertNotEqual(first, second)
    self.assertNotEqual(second, third)
```

Also update `tests/test_cyclewash_viewer_embed_layout.py` to require a two-row viewer-only layout:

```python
self.assertIn(
    'body[data-viewer-only="true"] .viewer-grid { height: 100vh;',
    viewer,
)
self.assertIn("grid-template-rows: auto minmax(0, 1fr);", viewer)
self.assertNotIn("280px", viewer)
```

Import `patch` from `unittest.mock` in `tests/test_cyclewash_technical_report_html.py`.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```powershell
python -m unittest tests.test_cyclewash_viewer_embed_layout tests.test_cyclewash_technical_report_html.CycleWashTechnicalReportHtmlTests.test_viewer_only_uses_horizontal_toolbar_above_full_width_canvas tests.test_cyclewash_technical_report_html.CycleWashTechnicalReportHtmlTests.test_viewer_asset_fingerprint_changes_with_template_or_runtime -v
```

Expected: FAIL because the controls follow the canvas, the old `280px` side column remains, startup status is hidden, and `viewer_asset_fingerprint` does not exist.

- [ ] **Step 3: Implement the deterministic viewer asset fingerprint**

Add `hashlib` to the imports and define this public helper beside the asset constants:

```python
def viewer_asset_fingerprint() -> str:
    """Hash every non-Python asset that changes generated viewer HTML."""

    digest = hashlib.sha256()
    for path in (TEMPLATE_PATH, THREE_BUNDLE_PATH):
        content = path.read_bytes()
        digest.update(path.name.encode("utf-8"))
        digest.update(len(content).to_bytes(8, byteorder="big"))
        digest.update(content)
    return digest.hexdigest()
```

Export it with the existing builders:

```python
__all__ = [
    "build_offline_report_html",
    "build_scenario_viewer_html",
    "viewer_asset_fingerprint",
]
```

- [ ] **Step 4: Replace the side panel with a horizontal toolbar and full-width scene**

Move `.controls` before `.scene-wrap` in the template and assign explicit grid areas:

```html
<div class="viewer-grid">
  <div class="controls" aria-label="Playback controls">
    <div class="scenario-selector" id="scenario-selector" role="group" aria-label="Fixed operating scenario">
      <button type="button" data-scenario="Gentle">Gentle</button>
      <button type="button" data-scenario="Normal">Normal</button>
      <button type="button" data-scenario="Heavy">Heavy</button>
    </div>
    <div class="control-row playback-control"><label for="play-pause">Playback</label><button id="play-pause" type="button">Pause</button></div>
    <div class="control-row phase-control"><label for="phase-slider">Phase <output id="phase-output">0 deg</output></label><input id="phase-slider" type="range" min="0" max="360" value="0" step="1"></div>
    <div class="control-row speed-control"><label for="speed-select">Playback speed</label><select id="speed-select"><option value="0.25">0.25x</option><option value="0.5">0.5x</option><option value="1" selected>1x</option><option value="2">2x</option></select></div>
  </div>
  <div class="scene-wrap">
    <canvas id="scene" tabindex="0" aria-label="CycleWash 3D assembly viewer"></canvas>
    <p id="viewer-status" class="viewer-status" role="status" aria-live="polite">Viewer starting...</p>
    <p class="scene-label">Gravity-referenced water body with bounded schematic slosh. Door closed at 50% transparency.</p>
    <div id="water-body" hidden></div>
    <div id="imbalance-vector" hidden></div>
  </div>
</div>
```

Replace the split-column CSS with:

```css
.viewer-grid { display: grid; grid-template-rows: auto minmax(460px, 1fr); gap: 12px; margin-top: 14px; }
.controls { display: grid; grid-template-columns: minmax(240px, 1fr) minmax(110px, .35fr) minmax(260px, 1.5fr) minmax(120px, .45fr); grid-template-areas: "scenarios playback phase speed"; gap: 12px; align-items: end; }
.scenario-selector { grid-area: scenarios; }
.playback-control { grid-area: playback; }
.phase-control { grid-area: phase; }
.speed-control { grid-area: speed; }
.scene-wrap { width: 100%; min-width: 0; }
.viewer-status { position: absolute; inset: 12px auto auto 12px; z-index: 2; margin: 0; padding: 8px 10px; border: 1px solid #64748b; background: rgba(15, 23, 42, .92); color: #f8fafc; }
body[data-viewer-only="true"] .viewer-grid { height: 100vh; margin-top: 0; grid-template-rows: auto minmax(0, 1fr); }
body[data-viewer-only="true"] .controls { grid-template-columns: minmax(110px, .35fr) minmax(260px, 1.5fr) minmax(120px, .45fr); grid-template-areas: "playback phase speed"; }
```

At `max-width: 760px`, use two toolbar rows without changing scene width:

```css
.controls { grid-template-columns: 1fr 1fr; grid-template-areas: "scenarios scenarios" "playback speed" "phase phase"; }
body[data-viewer-only="true"] .controls { grid-template-columns: 1fr 1fr; grid-template-areas: "playback speed" "phase phase"; }
body[data-viewer-only="true"] .viewer-grid { grid-template-rows: auto minmax(0, 1fr); }
```

- [ ] **Step 5: Make startup success and failure states explicit**

Keep the status visible from initial HTML through initialization. Hide it only after two successful rendered frames, and reveal it for every failure:

```javascript
function maybePass() {
  if (state.frames < 2 || !state.controls) return;
  if (state.failures.length || state.network.length) {
    body.dataset.viewerSmoke = "failed";
    return;
  }
  body.dataset.viewerSmoke = "passed";
  body.dataset.viewerSmokeFrames = String(state.frames);
  body.dataset.viewerSmokeScenario = state.controls.scenario;
  body.dataset.viewerSmokePlaying = String(state.controls.playing);
  body.dataset.viewerSmokePhase = String(state.controls.phase);
  body.dataset.viewerSmokeSpeed = String(state.controls.speed);
  viewerStatus.textContent = "Viewer ready";
  viewerStatus.hidden = true;
}
```

Remove the later `viewerStatus.textContent = "Viewer starting";` assignment because the initial markup now owns that state. Preserve `fail(...)` and the outer `catch` behavior that sets `hidden = false` and a concise error message.

- [ ] **Step 6: Run viewer unit and runtime smoke tests**

Run:

```powershell
python -m unittest tests.test_cyclewash_viewer_embed_layout tests.test_cyclewash_technical_report_html -v
```

Expected: PASS. The headless browser test must report two frames, zero failures, and zero network requests; it may skip only when neither Chrome nor Edge is installed.

- [ ] **Step 7: Commit the viewer deliverable**

```powershell
git add cyclewash_technical_report_html.py cyclewash_technical_report_template.html tests/test_cyclewash_technical_report_html.py tests/test_cyclewash_viewer_embed_layout.py
git commit -m "fix: restore full-width technical viewer"
```

---

### Task 2: Streamlit Page Order And Cache Invalidation

**Files:**
- Modify: `cyclewash_technical_evaluation_app.py:20-55, 190-230`
- Modify: `tests/test_cyclewash_technical_evaluation_app.py:15-130`

**Interfaces:**
- Consumes: `viewer_asset_fingerprint() -> str` from Task 1.
- Changes: `_cached_viewer_html(selected_name: str, fea_root: str, stl_root: str, asset_fingerprint: str) -> str`.
- Preserves: Streamlit Gentle/Normal/Heavy segmented control as the selected-scenario source of truth.
- Produces page order: title, scenario control, iframe, five metrics, four equations, comparison, one limitation, downloads.

- [ ] **Step 1: Add failing source-order and cache-key tests**

Extend `CycleWashTechnicalEvaluationAppTests`:

```python
def test_viewer_precedes_metrics_and_uses_asset_versioned_cache(self) -> None:
    from cyclewash_technical_evaluation_app import main

    source = Path(main.__code__.co_filename).read_text(encoding="utf-8")
    main_body = source[source.index("def main()") :]

    self.assertLess(
        main_body.index("st.iframe("),
        main_body.index("_render_selected_metrics(document)"),
    )
    self.assertIn("viewer_asset_fingerprint()", main_body)
    self.assertIn("width=\"stretch\"", main_body)


def test_viewer_cache_function_accepts_asset_fingerprint(self) -> None:
    import inspect
    from cyclewash_technical_evaluation_app import _cached_viewer_html

    parameter_names = tuple(inspect.signature(_cached_viewer_html).parameters)
    self.assertEqual(
        ("selected_name", "fea_root", "stl_root", "asset_fingerprint"),
        parameter_names,
    )
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```powershell
python -m unittest tests.test_cyclewash_technical_evaluation_app.CycleWashTechnicalEvaluationAppTests.test_viewer_precedes_metrics_and_uses_asset_versioned_cache tests.test_cyclewash_technical_evaluation_app.CycleWashTechnicalEvaluationAppTests.test_viewer_cache_function_accepts_asset_fingerprint -v
```

Expected: FAIL because metrics currently precede the iframe and the viewer cache has only three arguments.

- [ ] **Step 3: Add the viewer asset digest to the Streamlit cache key**

Import the new helper and extend the cached function signature:

```python
from cyclewash_technical_report_html import (
    build_offline_report_html,
    build_scenario_viewer_html,
    viewer_asset_fingerprint,
)


@st.cache_data(show_spinner=False)
def _cached_viewer_html(
    selected_name: str,
    fea_root: str,
    stl_root: str,
    asset_fingerprint: str,
) -> str:
    """Cache STL parsing and HTML by scenario and embedded asset version."""

    if not asset_fingerprint:
        raise ValueError("asset_fingerprint must not be empty")
    document = _cached_report_document(selected_name, fea_root)
    return build_scenario_viewer_html(document, selected_name, stl_root)
```

- [ ] **Step 4: Put the full-width viewer before the metric row**

Update the load and render sequence in `main()`:

```python
viewer_version = viewer_asset_fingerprint()
try:
    document = _cached_report_document(selected_name, fea_root)
    viewer_html = _cached_viewer_html(
        selected_name,
        fea_root,
        stl_root,
        viewer_version,
    )
except (OSError, RuntimeError, TypeError, ValueError):
    st.error(
        "Technical evaluation could not load its local report or STL assets. "
        "Confirm the CycleWash project files are complete, then reload this page."
    )
    return

st.iframe(viewer_html, height=610, width="stretch")
_render_selected_metrics(document)
```

Keep the four `core_formulas(document)` outputs, comparison table, single `LIMITATIONS_NOTE`, PDF, and offline HTML actions after the metrics.

- [ ] **Step 5: Run the Streamlit page tests**

Run:

```powershell
python -m unittest tests.test_cyclewash_technical_evaluation_app -v
```

Expected: PASS with no Streamlit exceptions, no tabs, one three-option scenario selector, five metrics, four formula subheaders, one comparison header, one limitations note, and two downloads.

- [ ] **Step 6: Commit the continuous-page deliverable**

```powershell
git add cyclewash_technical_evaluation_app.py tests/test_cyclewash_technical_evaluation_app.py
git commit -m "fix: order technical evaluation around full-width viewer"
```

---

### Task 3: Hosted Analytical Preview For Custom Structural Inputs

**Files:**
- Modify: `cyclewash_structural_app.py:1-145, 1900-2130`
- Modify: `cyclewash_engineering_model.py:320-370`
- Create: `tests/test_cyclewash_structural_app.py`

**Interfaces:**
- Produces immutable `FeaActionState(mode: str, action_label: str | None, notice: str)`.
- Produces `resolve_fea_action_state(*, cached_package_available: bool, solver_available: bool) -> FeaActionState`.
- Produces `build_stage1_analytical_preview(parts: Sequence[AssemblyPart], inputs: EngineeringInputs, analytical: AnalyticalResults, colorscale: str, phase_degrees: float) -> tuple[go.Figure, str]`.
- Consumes existing `calculate_engineering_loads`, `build_structural_figure`, `format_fea_engineering_summary`, authoritative STL `animation_parts`, Blender X shaft axis, and current fill/relief values.
- State precedence: exact cache, then local solver, then hosted analytical preview.

- [ ] **Step 1: Add failing unit tests for all three availability states**

Create `tests/test_cyclewash_structural_app.py`:

```python
"""Structural-page contracts for FEA availability and hosted preview behavior."""

from __future__ import annotations

from pathlib import Path
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
```

- [ ] **Step 2: Add a failing Streamlit integration test for non-default fill and relief**

Append this test to the same class:

```python
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
```

- [ ] **Step 3: Run the new tests and verify they fail**

Run:

```powershell
python -m unittest tests.test_cyclewash_structural_app -v
```

Expected: FAIL because `FeaActionState`, `resolve_fea_action_state`, and the automatic analytical preview do not exist; the custom-input page currently has a disabled FEA button and returns before rendering a load map.

- [ ] **Step 4: Implement the explicit availability state**

Import `AnalyticalResults` and add the immutable state and resolver near the existing display dataclasses:

```python
from cyclewash_engineering_model import (
    AnalyticalResults,
    EngineeringInputs,
    MaterialProperties,
    calculate_engineering_loads,
    format_fea_engineering_summary,
)


@dataclass(frozen=True)
class FeaActionState:
    """One honest action state for the current canonical FEA request."""

    mode: str
    action_label: str | None
    notice: str


def resolve_fea_action_state(
    *,
    cached_package_available: bool,
    solver_available: bool,
) -> FeaActionState:
    if cached_package_available:
        return FeaActionState(
            mode="cache",
            action_label="Load Cached Stage 1 FEA",
            notice="An exact solved Stage 1 FEA package matches these inputs.",
        )
    if solver_available:
        return FeaActionState(
            mode="solve",
            action_label="Run Stage 1 FEA",
            notice="The local Stage 1 FEA environment is available.",
        )
    return FeaActionState(
        mode="analytical",
        action_label=None,
        notice=(
            "Analytical preview updates for these inputs. Solved Stage 1 FEA for "
            "this combination must be run locally."
        ),
    )
```

- [ ] **Step 5: Build the analytical STL load-map helper**

Add a pure builder that reuses the current authoritative STL parts and existing engineering formatter:

```python
def build_stage1_analytical_preview(
    parts: Sequence[AssemblyPart],
    inputs: EngineeringInputs,
    analytical: AnalyticalResults,
    colorscale: str,
    phase_degrees: float,
) -> tuple[go.Figure, str]:
    """Build a geometric STL load map for a valid unsolved input request."""

    part_list = list(parts)
    if not part_list:
        raise ValueError("analytical preview requires loaded STL parts")
    rotation_axis = (1.0, 0.0, 0.0)
    rotation_origin = infer_rotating_axis_origin(part_list)
    rotation_angle = float(phase_degrees) % 360.0
    display_parts = [
        _apply_motion_pose(
            part,
            rotation_axis=rotation_axis,
            rotation_origin=rotation_origin,
            rotation_angle_degrees=rotation_angle,
            door_axis=(0.0, 0.0, -1.0),
            door_angle_degrees=0.0,
            door_hinge_origin=(0.0, 0.0, 0.0),
        )
        for part in part_list
    ]
    load_case = "Spin + water circulation through drum holes"
    presentation = load_case_presentation(display_parts, load_case)
    figure = build_structural_figure(
        parts=display_parts,
        presentation=presentation,
        load_case=load_case,
        colorscale=colorscale,
        casing_axis=(0.0, 0.0, -1.0),
        rotation_axis=rotation_axis,
        rotation_origin=rotation_origin,
        rotation_angle_degrees=rotation_angle,
        drum_speed_rpm=inputs.speed_rpm,
        water_fill_fraction=inputs.fill_fraction,
        perforation_relief=inputs.perforation_relief,
        door_axis=(0.0, 0.0, -1.0),
        door_hinge_origin=(0.0, 0.0, 0.0),
    )
    summary = format_fea_engineering_summary(
        inputs,
        analytical,
        package_summary=(
            "Analytical preview from the current STL geometry and reduced-order "
            "loads. No solved FEA package is attached."
        ),
    )
    return figure, summary
```

- [ ] **Step 6: Replace the disabled button with the resolved action**

Treat the missing optional solver as normal hosted state rather than a warning:

```python
status = detect_fea_solver(PROJECT_ROOT)
if status.available:
    version_text = ", ".join(
        f"{name} {version}" for name, version in status.versions.items()
    )
    st.success(f"{status.message} {version_text}")
else:
    st.caption("Optional local Stage 1 FEA solver is not installed in this environment.")
```

After computing `cached_package_available`, resolve and render the state:

```python
action_state = resolve_fea_action_state(
    cached_package_available=cached_package_available,
    solver_available=status.available,
)
if action_state.mode == "analytical":
    st.info(action_state.notice)
    action_requested = False
else:
    st.caption(action_state.notice)
    assert action_state.action_label is not None
    action_requested = st.button(action_state.action_label, type="primary")

if action_requested:
    previous_path = st.session_state.get(SELECTED_FEA_PACKAGE_SESSION_KEY)
    progress_bar = st.progress(0.0, text="0% - Preparing Stage 1 FEA")
    live_status = st.status("Preparing Stage 1 FEA", expanded=False)
    progress_adapter = StreamlitFeaProgress(progress_bar, live_status)
    try:
        if cached_package_available:
            try:
                package = require_matching_package(
                    load_stage1_package(expected_path), inputs, mesh_levels
                )
            except (OSError, ValueError):
                if not status.available:
                    raise
                package = run_fea_subprocess(
                    inputs,
                    FEA_RESULT_ROOT,
                    mesh_levels,
                    progress_callback=progress_adapter,
                )
        else:
            package = run_fea_subprocess(
                inputs,
                FEA_RESULT_ROOT,
                mesh_levels,
                progress_callback=progress_adapter,
            )
        require_matching_package(package, inputs, mesh_levels)
        require_matching_package(
            load_stage1_package(expected_path), inputs, mesh_levels
        )
    except (FeaRunnerError, OSError, ValueError) as error:
        st.session_state[SELECTED_FEA_PACKAGE_SESSION_KEY] = (
            updated_selected_package_path(
                previous_path,
                expected_path,
                solve_succeeded=False,
            )
        )
        progress_adapter.fail(f"Stage 1 FEA failed: {error}")
        st.error(f"Stage 1 FEA did not produce a selectable package: {error}")
    else:
        st.session_state[SELECTED_FEA_PACKAGE_SESSION_KEY] = str(
            updated_selected_package_path(
                previous_path,
                expected_path,
                solve_succeeded=True,
            )
        )
        progress_adapter.complete()
```

Do not render a disabled FEA action in analytical mode. This keeps exact package validation, failure handling, session-state selection, and subprocess behavior identical for the cache and local-solver states.

- [ ] **Step 7: Render the analytical preview instead of returning on no package**

In the existing `if package is None:` branch, preserve package rejection errors. For the expected hosted state, render the current STL load map and copyable summary:

```python
if package is None:
    if package_load_error is not None:
        st.error(
            "Selected FEA package was rejected for visualization; HTML export will use "
            f"the geometric preview fallback. Details: {package_load_error}"
        )
        return
    if action_state.mode == "analytical":
        st.subheader("Analytical preview")
        figure, analytical_summary = build_stage1_analytical_preview(
            animation_parts,
            inputs,
            analytical,
            fea_animation_colorscale,
            float(requested_phase),
        )
        viewer_column, summary_column = st.columns([1.45, 1.0], gap="large")
        with viewer_column:
            st.plotly_chart(figure, width="stretch")
        with summary_column:
            st.subheader("Analytical Calculation Summary")
            st.code(analytical_summary, language="text", wrap_lines=True)
        return
    st.info("Load the exact cached request or run the local solver to view physical FEA results.")
    return
```

This path uses the already recalculated `analytical` object, so fill and relief immediately update retained mass, hydrostatic/centrifugal/design pressure, load colors, and the code block.

- [ ] **Step 8: Include current fill and relief in the copyable analytical summary**

Add the fill line immediately before the existing perforation-relief line in `format_fea_engineering_summary`:

```diff
 - A transient design factor of {inputs.transient_factor:.2f} multiplies torque.
+- Drum water fill is {inputs.fill_fraction:.0%} of the nominal internal volume.
 - The model applies {inputs.perforation_relief:.0%} perforation relief to retained mass and centrifugal pressure.
```

The existing Water Model section already reports `m_water`, `p_h`, `p_c`, and `p_design`; leave those evaluated values in place.

- [ ] **Step 9: Run structural and engineering regression tests**

Run:

```powershell
python -m unittest tests.test_cyclewash_structural_app tests.test_cyclewash_scenarios tests.test_cyclewash_fea_results -v
```

Expected: PASS. The custom 47% fill / 38% relief case has no disabled solver action, shows `Analytical preview`, renders a Plotly figure, and produces a calculation summary with current inputs.

- [ ] **Step 10: Commit the hosted-preview deliverable**

```powershell
git add cyclewash_structural_app.py cyclewash_engineering_model.py tests/test_cyclewash_structural_app.py
git commit -m "fix: provide hosted analytical structural preview"
```

---

### Task 4: Deployment Contract And End-To-End Verification

**Files:**
- Modify: `README.md:1-80`
- Verify: `requirements.txt`
- Verify: `Gear_Builder.py`
- Verify: `pages/2_Structural_Load_Visualizer.py`
- Verify: `pages/3_Technical_Evaluation.py`

**Interfaces:**
- Documents public deployment as repository `marcuskempa/Cycle-Wash`, branch `main`, entrypoint `Gear_Builder.py`.
- Preserves dependency-light hosted requirements; `requirements_fea.txt` remains local and optional.
- Produces no new runtime dependencies.

- [ ] **Step 1: Add the exact Streamlit Community Cloud deployment note**

Replace the current abbreviated section with:

```markdown
## Streamlit Community Cloud

Use these deployment settings:

- Repository: `marcuskempa/Cycle-Wash`
- Branch: `main`
- Main file path: `Gear_Builder.py`

Community Cloud installs `requirements.txt` and automatically redeploys after a
new commit reaches `main`. The optional packages in `requirements_fea.txt` are
for local solved Stage 1 FEA only. When a hosted input combination has no exact
cached package, the Structural Load Visualizer shows a clearly labeled
`Analytical preview` and explains that a solved package must be generated
locally.
```

- [ ] **Step 2: Run the complete automated suite**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected: PASS. The report PDF test must still produce exactly two pages and exactly one limitations statement. The HTML runtime test must still report two rendered frames and no network requests.

- [ ] **Step 3: Start the multipage app locally for browser QA**

Run from the repository root:

```powershell
streamlit run Gear_Builder.py --server.headless true --server.port 8502
```

Expected: Streamlit reports `http://localhost:8502`; leave the process running until browser QA is complete.

- [ ] **Step 4: Verify the Technical Evaluation at desktop width**

Open `http://localhost:8502`, select `Technical Evaluation`, and verify at 1440 x 900:

1. There are no Presentation/Technical Report tabs.
2. The viewer spans the content width and the toolbar is above the canvas.
3. The assembly, enclosure, closed translucent door, water, laundry imbalance marker, drum, shaft, and gear are visible.
4. Play/Pause, phase scrubber, and 0.25x/0.5x/1x/2x speed controls work without Streamlit reruns.
5. Gentle, Normal, and Heavy each update the viewer and the five metrics below it.
6. Four core calculations, one comparison table, one limitation, and both downloads follow the metric row.
7. The browser console has no fresh errors and the canvas contains non-background pixels.

- [ ] **Step 5: Verify responsive layout at 390 px width**

Resize the browser viewport to 390 x 844 and verify:

1. The toolbar wraps without text overlap or horizontal page scrolling.
2. The canvas remains below the toolbar and spans the iframe width.
3. The five metrics wrap cleanly below the viewer.
4. Formula blocks and the comparison table remain readable.

- [ ] **Step 6: Verify Structural Load Visualizer hosted behavior**

Select `Structural Load Visualizer`, choose `Simplified Stage 1 FEA`, then set Drum fill to 47% and Perforation relief to 38%:

1. No disabled `Run Stage 1 FEA` button is shown.
2. The page displays `Analytical preview` and the local-solve explanation.
3. The STL-based load map remains visible.
4. The copyable summary contains the current fill, relief, retained water mass, and design pressure.
5. Returning to an exact cached input exposes `Load Cached Stage 1 FEA`.

- [ ] **Step 7: Verify both exports**

Download the PDF and offline HTML for Normal:

1. PDF is exactly two pages and contains one limitations note.
2. Offline HTML opens from disk with no network connection.
3. Offline scenario buttons, playback, phase, speed, and assembly rendering work.

- [ ] **Step 8: Commit documentation and verification-ready state**

```powershell
git add README.md
git commit -m "docs: record CycleWash deployment contract"
```

- [ ] **Step 9: Integrate and deploy after review**

After all task commits pass review, merge the feature branch into `main`, push `main`, and confirm Streamlit Community Cloud is configured for `marcuskempa/Cycle-Wash` / `main` / `Gear_Builder.py`. Reboot the deployed app once if the prior iframe HTML remains cached, then repeat the desktop viewer smoke check against the public URL and confirm it corresponds to the new GitHub `main` commit.
