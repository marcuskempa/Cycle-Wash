# CycleWash Technical Evaluation Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the washer animation coordinate system and internal-load placement, then replace the dense tabbed technical evaluation with one concise Streamlit page and an exact two-page PDF.

**Architecture:** Preserve the shared Blender gear-shaft origin as the X-axis rotation pivot and add an inner-drum geometric envelope to the HTML payload solely for positioning water and laundry. Centralize the four introductory formulas and one limitations statement in the report model, then consume that shared subset from Streamlit, offline HTML, and PDF renderers.

**Tech Stack:** Python 3.11+, Streamlit 1.59+, NumPy, ReportLab, Pillow, Three.js offline bundle, `unittest`, Plotly/STL utilities already present in the repository.

## Global Constraints

- All STL meshes retain the shared rear gear-shaft origin; no mesh receives a replacement pivot.
- Blender X is the rotation axis, Blender Z is vertical, and gravity points along negative Z.
- Only Play/Pause, phase, playback speed, and the standalone-HTML scenario selector remain inside the viewer.
- Streamlit presents one continuous page with no Presentation/Technical Report tabs and no duplicated metrics.
- The only report equations are gear ratio, drum-edge velocity, unbalanced laundry force, and combined stress/factor of safety.
- The PDF contains exactly two pages and one limitations statement.
- The limitations statement identifies the calculations as simplified analytical estimates, not validated structural FEA or CFD.
- Existing offline HTML byte/triangle budgets and malformed-STL error handling remain active.

---

### Task 1: Correct Viewer Coordinates And Internal Loads

**Files:**
- Modify: `cyclewash_technical_report_html.py:102-145`
- Modify: `cyclewash_technical_report_template.html:209-350`
- Test: `tests/test_cyclewash_technical_report_html.py`

**Interfaces:**
- Consumes: normalized `AssemblyPart` objects and the shared shaft-derived `rotation_origin`.
- Produces: payload field `geometry.drum_envelope` with `center_m: list[float]` and `span_m: list[float]`.
- Produces: a Z-up Three.js scene whose water and laundry positions derive from the drum envelope while rotation remains about `rotation_origin`.

- [ ] **Step 1: Add failing payload and runtime-contract tests**

Add tests that parse `cyclewash-report-data` and require the new envelope and coordinate contract:

```python
def test_payload_preserves_shaft_pivot_and_adds_drum_envelope(self) -> None:
    payload = _payload_from_html(self.html)
    envelope = payload["geometry"]["drum_envelope"]

    self.assertEqual(3, len(payload["geometry"]["rotation_origin"]))
    self.assertEqual(3, len(envelope["center_m"]))
    self.assertEqual(3, len(envelope["span_m"]))
    self.assertTrue(all(value > 0.0 for value in envelope["span_m"]))

def test_viewer_uses_blender_z_up_and_drum_relative_contents(self) -> None:
    self.assertIn("camera.up.set(0, 0, 1)", self.html)
    self.assertIn("grid.rotation.x = Math.PI / 2", self.html)
    self.assertIn("payload.geometry.drum_envelope.center_m", self.html)
    self.assertIn("const laundryBase = drumCenter.clone().sub(origin)", self.html)
    self.assertIn("water.position.copy(drumCenter)", self.html)
    self.assertNotIn("new THREE.BoxGeometry(0.55, 0.16, 0.34)", self.html)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m unittest tests.test_cyclewash_technical_report_html.CycleWashTechnicalReportHtmlTests.test_payload_preserves_shaft_pivot_and_adds_drum_envelope tests.test_cyclewash_technical_report_html.CycleWashTechnicalReportHtmlTests.test_viewer_uses_blender_z_up_and_drum_relative_contents -v
```

Expected: FAIL because `drum_envelope`, Z-up camera setup, and drum-relative content placement do not exist.

- [ ] **Step 3: Add the inner-drum envelope to the payload**

In `_build_payload`, derive bounds from the already normalized `Inner Drum` part while leaving shaft-origin inference unchanged:

```python
inner_drum = next(part for part in parts if _normalized_name(part.name) == "inner drum")
drum_vertices = np.asarray(inner_drum.vertices, dtype=float)
drum_minimum = drum_vertices.min(axis=0)
drum_maximum = drum_vertices.max(axis=0)
drum_span = drum_maximum - drum_minimum
if not np.all(np.isfinite(drum_span)) or np.any(drum_span <= 0.0):
    raise ValueError("normalized Inner Drum geometry must have finite non-zero bounds")
drum_center = (drum_minimum + drum_maximum) / 2.0
```

Add this exact payload shape:

```python
"drum_envelope": {
    "center_m": [float(value) for value in drum_center],
    "span_m": [float(value) for value in drum_span],
},
```

- [ ] **Step 4: Correct the Three.js coordinate system and content placement**

Set Blender Z as camera-up and place the floor grid in XY:

```javascript
camera.up.set(0, 0, 1);
camera.position.set(1.15, -0.92, 0.82);
camera.lookAt(0, 0, 0);
grid.rotation.x = Math.PI / 2;
```

Replace the hard-coded water block and shaft-origin laundry location with drum-envelope values:

```javascript
const drumCenter = new THREE.Vector3().fromArray(payload.geometry.drum_envelope.center_m);
const drumSpan = new THREE.Vector3().fromArray(payload.geometry.drum_envelope.span_m);
const water = new THREE.Mesh(
  new THREE.SphereGeometry(1, 32, 18),
  new THREE.MeshStandardMaterial({ color: 0x0ea5e9, transparent: true, opacity: 0.42, roughness: 0.25 })
);
scene.add(water);
const laundryBase = drumCenter.clone().sub(origin);
```

In `updateScene`, keep the marker inside the rotating drum and the water in world space:

```javascript
laundry.position.copy(laundryBase).add(new THREE.Vector3(0, selected.eccentricity_m, 0));
water.scale.set(
  drumSpan.x * 0.38,
  drumSpan.y * 0.38,
  drumSpan.z * 0.38 * Math.max(0.18, selected.fill_fraction)
);
water.position.copy(drumCenter);
water.position.z -= drumSpan.z * 0.18 * (1 - selected.fill_fraction);
water.rotation.x = Math.sin(radians - Math.PI / 4) * (8 * Math.PI / 180);
```

Keep `rotor.position.copy(origin)` and `rotor.rotation.x = radians` unchanged so the shared gear-shaft pivot remains authoritative.

- [ ] **Step 5: Run HTML tests and verify GREEN**

Run:

```powershell
python -m unittest tests.test_cyclewash_technical_report_html -v
```

Expected: PASS, except the documented optional local Chrome timeout skip when that executable does not return.

- [ ] **Step 6: Commit Task 1**

```powershell
git add cyclewash_technical_report_html.py cyclewash_technical_report_template.html tests/test_cyclewash_technical_report_html.py
git commit -m "fix: align technical viewer with drum geometry"
```

---

### Task 2: Centralize Introductory Content And Combine The Streamlit Page

**Files:**
- Modify: `cyclewash_technical_report.py:1-540`
- Modify: `cyclewash_technical_evaluation_app.py:1-310`
- Test: `tests/test_cyclewash_technical_report.py`
- Test: `tests/test_cyclewash_technical_evaluation_app.py`

**Interfaces:**
- Produces: `CORE_FORMULA_IDS: tuple[str, ...]`.
- Produces: `LIMITATIONS_NOTE: str`.
- Produces: `core_formulas(document: ReportDocument) -> tuple[FormulaDefinition, ...]`.
- Consumes: `build_scenario_viewer_html`, the selected report document, and existing cached exporters.

- [ ] **Step 1: Add failing shared-content tests**

Require the exact formula subset and one limitations statement:

```python
def test_introductory_report_subset_contains_four_core_formulas(self) -> None:
    from cyclewash_technical_report import CORE_FORMULA_IDS, LIMITATIONS_NOTE, core_formulas

    formulas = core_formulas(self.document)
    self.assertEqual(CORE_FORMULA_IDS, tuple(formula.identifier for formula in formulas))
    self.assertEqual(4, len(formulas))
    self.assertIn("not validated structural FEA or CFD", LIMITATIONS_NOTE)
```

- [ ] **Step 2: Add failing combined-page AppTest assertions**

Replace the old tab expectations with:

```python
self.assertEqual([], app.tabs)
self.assertEqual(5, len(app.metric))
self.assertEqual(
    ["Core Engineering Checks", "Scenario Comparison"],
    [header.value for header in app.header],
)
self.assertEqual(1, sum("not validated structural FEA or CFD" in item.value for item in app.info))
for removed in ("Exact Cached FEA", "Project Dimensions And Drivetrain", "Assumptions", "Provenance"):
    self.assertNotIn(removed, " ".join(item.value for item in app.markdown))
```

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```powershell
python -m unittest tests.test_cyclewash_technical_report.CycleWashTechnicalReportTests.test_introductory_report_subset_contains_four_core_formulas tests.test_cyclewash_technical_evaluation_app -v
```

Expected: FAIL because the shared subset does not exist and the page still has two tabs and long-form sections.

- [ ] **Step 4: Add the shared introductory-content API**

In `cyclewash_technical_report.py`, define:

```python
CORE_FORMULA_IDS = (
    "drivetrain_speed_ratio",
    "angular_speed_and_edge_velocity",
    "unbalanced_wet_laundry_load",
    "combined_stress_and_factor_of_safety",
)

LIMITATIONS_NOTE = (
    "These results are simplified analytical estimates for an introductory design study, "
    "not validated structural FEA or CFD. The model lacks the detailed materials, boundary "
    "conditions, contacts, turbulence, and mesh refinement required for engineering validation."
)

def core_formulas(document: ReportDocument) -> tuple[FormulaDefinition, ...]:
    by_identifier = {formula.identifier: formula for formula in document.formulas}
    return tuple(by_identifier[identifier] for identifier in CORE_FORMULA_IDS)
```

Export these names in `__all__`.

- [ ] **Step 5: Replace tabs with one continuous Streamlit flow**

Render metrics in a single row:

```python
columns = st.columns(len(metrics))
for column, (label, value) in zip(columns, metrics, strict=True):
    column.metric(label, value)
```

Make `_render_formula` concise by rendering equation, evaluated substitution, and a one-line variable definition:

```python
st.subheader(formula.title)
_render_latex(formula.latex)
st.caption(formula.evaluated)
definitions = "; ".join(
    f"{symbol.symbol}: {symbol.meaning} [{symbol.unit}]" for symbol in formula.symbols
)
st.caption(f"Variables: {definitions}")
```

Replace the two-tab block in `main` with this exact page sequence:

```python
_render_selected_metrics(document)
st.iframe(viewer_html, height=610)
st.header("Core Engineering Checks")
for formula in core_formulas(document):
    _render_formula(formula)
_render_comparison(document)
st.info(LIMITATIONS_NOTE)
_render_downloads(selected_name, fea_root, stl_root)
```

Reduce comparison columns to Scenario, RPM, Water mass, Imbalance force, Shaft stress, and FoS. Remove `_render_presentation_equations`, `_render_technical_report`, the cached-FEA UI block, repeated provenance captions, assumptions, project-dimension table, interpretation, and conclusion from this page.

- [ ] **Step 6: Run report and Streamlit tests and verify GREEN**

Run:

```powershell
python -m unittest tests.test_cyclewash_technical_report tests.test_cyclewash_technical_evaluation_app -v
```

Expected: PASS with the three Streamlit entrypoints still loading successfully.

- [ ] **Step 7: Commit Task 2**

```powershell
git add cyclewash_technical_report.py cyclewash_technical_evaluation_app.py tests/test_cyclewash_technical_report.py tests/test_cyclewash_technical_evaluation_app.py
git commit -m "feat: simplify technical evaluation page"
```

---

### Task 3: Simplify The Embedded And Offline HTML Presentation

**Files:**
- Modify: `cyclewash_technical_report_html.py:237-359`
- Modify: `cyclewash_technical_report_template.html:7-100`
- Test: `tests/test_cyclewash_technical_report_html.py`

**Interfaces:**
- Consumes: `core_formulas(document)` and `LIMITATIONS_NOTE` from `cyclewash_technical_report.py`.
- Preserves: standalone scenario controls and the existing single-file offline runtime.
- Produces: a viewer-only document with no embedded headings, captions, or metrics and a concise standalone report outside the viewer.

- [ ] **Step 1: Add failing embedded/standalone content tests**

```python
def test_embedded_viewer_only_exposes_animation_controls(self) -> None:
    viewer = build_scenario_viewer_html(self.document, "Normal", PROJECT_ROOT)
    self.assertIn('data-viewer-only="true"', viewer)
    self.assertIn('body[data-viewer-only="true"] .viewer-header', viewer)
    self.assertIn('body[data-viewer-only="true"] .metrics', viewer)
    self.assertIn('body[data-viewer-only="true"] .scene-label', viewer)
    self.assertIn('id="play-pause"', viewer)
    self.assertIn('id="phase-slider"', viewer)
    self.assertIn('id="speed-select"', viewer)

def test_offline_report_contains_only_core_equations_and_one_limitation(self) -> None:
    for formula in core_formulas(self.document):
        self.assertIn(formula.title, self.html)
    self.assertNotIn("Exact FEA Result And Provenance", self.html)
    self.assertNotIn("Project Dimensions And Drivetrain", self.html)
    self.assertEqual(1, self.html.count(LIMITATIONS_NOTE))
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
python -m unittest tests.test_cyclewash_technical_report_html.CycleWashTechnicalReportHtmlTests.test_embedded_viewer_only_exposes_animation_controls tests.test_cyclewash_technical_report_html.CycleWashTechnicalReportHtmlTests.test_offline_report_contains_only_core_equations_and_one_limitation -v
```

Expected: FAIL because viewer-only text/metrics and the long report remain.

- [ ] **Step 3: Hide duplicated viewer content in embedded mode**

Add viewer-only CSS:

```css
body[data-viewer-only="true"] .viewer-header,
body[data-viewer-only="true"] .metrics,
body[data-viewer-only="true"] .scene-label { display: none; }
body[data-viewer-only="true"] .viewer-shell { padding: 0; }
body[data-viewer-only="true"] .viewer-grid { margin-top: 0; }
```

Keep the scenario selector hidden only in viewer-only mode. Keep Play/Pause, phase, and speed controls visible.

- [ ] **Step 4: Replace the long standalone report renderer**

Change `_render_report` to emit:

1. One report header and selected-scenario summary.
2. One compact scenario comparison table.
3. Four formula sections from `core_formulas(document)` using title, HTML equation, evaluated substitution, and `symbol: meaning [unit]` definitions.
4. One `LIMITATIONS_NOTE` block.
5. One short conclusion.

Do not emit project-dimension, cached-FEA, assumptions, repeated provenance, or full formula-catalogue sections.

- [ ] **Step 5: Run all HTML tests and verify GREEN**

Run:

```powershell
python -m unittest tests.test_cyclewash_technical_report_html -v
```

Expected: PASS, with no outbound URL references and deterministic bytes unchanged between repeated builds.

- [ ] **Step 6: Commit Task 3**

```powershell
git add cyclewash_technical_report_html.py cyclewash_technical_report_template.html tests/test_cyclewash_technical_report_html.py
git commit -m "feat: streamline offline technical report"
```

---

### Task 4: Produce An Exact Two-Page PDF

**Files:**
- Modify: `cyclewash_technical_report_pdf.py:1-608`
- Test: `tests/test_cyclewash_technical_report_pdf.py`

**Interfaces:**
- Consumes: `core_formulas(document)` and `LIMITATIONS_NOTE`.
- Produces: deterministic PDF bytes with exactly two `/Type /Page` objects and one embedded assembly image.

- [ ] **Step 1: Replace long-report PDF assertions with failing two-page requirements**

```python
def test_builds_exact_two_page_introductory_report(self) -> None:
    pdf_bytes = build_report_pdf(self.document, PROJECT_ROOT)
    report_text = " ".join(_extract_pdf_text(pdf_bytes).split())

    self.assertEqual(2, len(re.findall(rb"/Type\s*/Page\b", pdf_bytes)))
    self.assertEqual(1, len(re.findall(rb"/Subtype\s*/Image\b", pdf_bytes)))
    for formula in core_formulas(self.document):
        self.assertIn(formula.title, report_text)
    self.assertEqual(1, report_text.count(LIMITATIONS_NOTE))
    for removed in (
        "Formula Catalogue",
        "Exact FEA Result And Provenance",
        "Assumptions",
        "Physical Geometry And Drivetrain Configuration",
    ):
        self.assertNotIn(removed, report_text)
```

- [ ] **Step 2: Run the PDF test and verify RED**

Run:

```powershell
python -m unittest tests.test_cyclewash_technical_report_pdf.CycleWashTechnicalReportPdfTests.test_builds_exact_two_page_introductory_report -v
```

Expected: FAIL because the current report has seven pages, three images, all formulas, and repeated limitations.

- [ ] **Step 3: Build the two-page story explicitly**

Import `PageBreak` and replace `_report_story` with two bounded sections.

Page 1 flowables:

```python
[
    Paragraph("CycleWash Technical Evaluation", styles["title"]),
    Paragraph(_summary_text(document), styles["body"]),
    _image_flowable(_assembly_figure(stl_root), CONTENT_WIDTH, 2.35 * inch),
    _metric_table(selected_metric_entries, styles),
    Paragraph("Operating Scenario Comparison", styles["section"]),
    _scenario_table(document.scenario_reports, styles),
    PageBreak(),
]
```

Page 2 flowables:

```python
[
    Paragraph("Core Engineering Calculations", styles["section"]),
    *[_compact_formula_block(formula, styles) for formula in core_formulas(document)],
    Paragraph("Engineering Interpretation", styles["section"]),
    Paragraph(document.engineering_interpretation, styles["body"]),
    Paragraph("Conclusion", styles["section"]),
    Paragraph(document.conclusion, styles["body"]),
    Paragraph("Limitations", styles["section"]),
    Paragraph(LIMITATIONS_NOTE, styles["body"]),
]
```

Implement `_compact_formula_block` as a `KeepTogether` containing the title, equation, evaluated substitution, and one compact definitions paragraph:

```python
definitions = "; ".join(
    f"{symbol.symbol}: {symbol.meaning} [{symbol.unit}]" for symbol in formula.symbols
)
return KeepTogether(
    [
        Paragraph(formula.title, styles["subsection"]),
        Paragraph(_equation_markup(formula.html), styles["equation"]),
        Paragraph(f"<b>Evaluated:</b> {formula.evaluated}", styles["small"]),
        Paragraph(f"<b>Variables:</b> {definitions}", styles["small"]),
        Spacer(1, 3),
    ]
)
```

Remove comparison-chart and imbalance-diagram flowables from the story so the PDF embeds only the assembly snapshot. Retain their helper functions only if another module imports them; otherwise delete them.

- [ ] **Step 4: Run PDF tests and tune only spacing until exactly two pages**

Run:

```powershell
python -m unittest tests.test_cyclewash_technical_report_pdf -v
```

Expected: PASS with deterministic two-page output and one image.

- [ ] **Step 5: Render both pages for visual inspection**

Generate the Normal report, render pages 1 and 2 to PNG with Poppler, and inspect both for clipping, table overlap, unreadable formula wrapping, and accidental third-page content.

- [ ] **Step 6: Commit Task 4**

```powershell
git add cyclewash_technical_report_pdf.py tests/test_cyclewash_technical_report_pdf.py
git commit -m "feat: condense engineering report to two pages"
```

---

### Task 5: Integration Verification And Publication

**Files:**
- Modify only if verification exposes a scoped defect.

**Interfaces:**
- Consumes: completed viewer, Streamlit, HTML, and PDF tasks.
- Produces: reviewed and published `main` branch.

- [ ] **Step 1: Run the full test suite**

```powershell
python -m unittest discover -s tests -p "test_cyclewash_*.py" -v
```

Expected: all tests PASS, with at most the documented local headless-Chrome timeout skip.

- [ ] **Step 2: Run static verification**

```powershell
git diff --check
python -m py_compile cyclewash_technical_report.py cyclewash_technical_report_html.py cyclewash_technical_report_pdf.py cyclewash_technical_evaluation_app.py
```

Expected: both commands exit successfully with no syntax or whitespace errors.

- [ ] **Step 3: Run desktop browser QA**

Start Streamlit, open Technical Evaluation, and verify:

- washer stands upright with Z vertical;
- gear-shaft origin remains the rotation pivot;
- water and laundry remain inside the drum through a full phase sweep;
- only playback, phase, and speed controls appear inside the viewer;
- scenario changes update the single metric row and four equations;
- there are no duplicated RPM/load displays and no browser console errors.

- [ ] **Step 4: Run narrow viewport QA**

At 390 x 844, verify no horizontal page overflow, clipped button labels, or overlapping equations/tables.

- [ ] **Step 5: Request final code review**

Review the complete diff from `2de6105..HEAD` for coordinate correctness, duplication, PDF page count, offline safety, regression risk, and missing tests. Resolve all Critical and Important findings.

- [ ] **Step 6: Merge and publish**

Fast-forward the approved branch into `main`, push `origin/main`, and confirm local `HEAD` equals `origin/main`.
