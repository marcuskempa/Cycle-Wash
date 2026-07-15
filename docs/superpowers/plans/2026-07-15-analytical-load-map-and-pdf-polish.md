# CycleWash Analytical Load Map And PDF Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace decorative viewer colors with phase-resolved analytical stress/pressure fields and publish a concise, cache-safe two-page PDF with a readable STL assembly image and paired symbolic/numerical equations.

**Architecture:** Extend the existing scenario payload with the analytical quantities already calculated by `cyclewash_engineering_model.py`, then evaluate per-vertex shaft/gear stress and drum pressure in the offline Three.js runtime. Keep report equations in the shared `FormulaDefinition` model by adding an evaluated LaTeX field, while the PDF renderer remains responsible for deterministic STL rasterization and its own version fingerprint.

**Tech Stack:** Python 3.12, NumPy, Streamlit, ReportLab, Pillow, embedded Three.js 0.185.1, `unittest`, Poppler browser/PDF smoke checks.

## Global Constraints

- Preserve the approved Gentle, Normal, and Heavy scenario values.
- Keep the Technical Evaluation offline-safe and self-contained.
- Keep the PDF exactly two A4 pages with one concise limitations statement.
- Do not add transient FEA, CFD, or a new external dependency.
- Keep enclosure and door neutral/translucent; rotate drum, agitator, shaft, gear, laundry marker, and force arrow around Blender X.

---

### Task 1: Phase-Resolved Analytical Load Map

**Files:**
- Modify: `cyclewash_technical_report_html.py`
- Modify: `cyclewash_technical_report_template.html`
- Test: `tests/test_cyclewash_technical_report_html.py`

**Interfaces:**
- Consumes: `ScenarioReport.results.analytical`, `ScenarioReport.results`, embedded STL position buffers, and `payload.geometry.rotation_origin`.
- Produces: scenario payload fields `shaft_static_bending_pa`, `shaft_imbalance_bending_pa`, `shaft_torsional_shear_pa`, `hydrostatic_pressure_pa`, `centrifugal_pressure_pa`, `design_pressure_pa`, and `slosh_amplification`; runtime vertex-color fields and engineering legend values.

- [ ] **Step 1: Write failing payload and runtime contract tests**

Add tests that assert every scenario payload contains finite positive stress/pressure inputs, the old decorative note and `setHSL` loop are absent, the template creates vertex color attributes, and the runtime exposes stress/pressure legend values.

```python
for scenario in payload["scenarios"].values():
    self.assertGreater(scenario["shaft_torsional_shear_pa"], 0.0)
    self.assertGreater(scenario["design_pressure_pa"], 0.0)
self.assertNotIn("animation color only", self.html)
self.assertIn('geometry.setAttribute("color"', self.html)
self.assertIn("updateAnalyticalLoadFields", self.html)
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```powershell
python -m unittest tests.test_cyclewash_technical_report_html -v
```

Expected: failures for missing scenario fields, missing analytical field functions/legends, and the still-present decorative note.

- [ ] **Step 3: Add analytical quantities to the scenario payload**

In `_scenario_payload`, derive the imbalance-only bending stress from the approved shaft diameter and imbalance moment:

```python
imbalance_bending_pa = (
    32.0 * results.imbalance_moment_n_m
    / (math.pi * results.inputs.shaft_diameter_m**3)
)
```

Serialize the existing static bending, torsional shear, hydrostatic pressure, centrifugal pressure, design pressure, and slosh-amplification values as finite floats.

- [ ] **Step 4: Implement per-vertex fields and legends**

Create vertex-color attributes for Inner Drum, shaft, and gear meshes. Recompute colors when scenario or phase changes:

```javascript
sigmaB = sigmaStatic + sigmaImbalance * Math.cos(phi - theta);
sigmaVm = Math.sqrt(sigmaB * sigmaB + 3 * tauT * tauT);
pressure = Math.min(
  pressureCeiling,
  (hydrostatic * normalizedDepth + centrifugal) * sloshAmplification
);
```

Map normalized values through one deterministic blue-cyan-yellow-red gradient, update buffer attributes with `needsUpdate = true`, and show compact MPa/kPa ranges in scene-overlay legends. Remove the decorative provenance sentence and whole-material HSL animation.

- [ ] **Step 5: Extend the browser smoke contract**

During `runRuntimeSmokeTest`, sample representative color data at phase 0 and phase 123 degrees and assert that at least one analytical field changes, all legend values are finite, and scenario/playback controls remain functional.

- [ ] **Step 6: Run the focused tests and commit Task 1**

Run the HTML test module and `git diff --check`; expected result is PASS with only the existing environment-dependent browser skip if Chrome does not complete.

```powershell
git add cyclewash_technical_report_html.py cyclewash_technical_report_template.html tests/test_cyclewash_technical_report_html.py
git commit -m "feat: animate analytical load fields"
```

---

### Task 2: Concise Symbolic And Evaluated Equations

**Files:**
- Modify: `cyclewash_technical_report.py`
- Modify: `cyclewash_technical_evaluation_app.py`
- Modify: `cyclewash_technical_report_html.py`
- Modify: `cyclewash_technical_report_pdf.py`
- Test: `tests/test_cyclewash_technical_report.py`
- Test: `tests/test_cyclewash_technical_evaluation_app.py`
- Test: `tests/test_cyclewash_technical_report_html.py`
- Test: `tests/test_cyclewash_technical_report_pdf.py`

**Interfaces:**
- Consumes: the four existing core `FormulaDefinition` records and their selected `ScenarioReport` values.
- Produces: `FormulaDefinition.evaluated_latex: str` and `FormulaDefinition.evaluated_html: str`, rendered as a second equation in Streamlit/offline HTML and ReportLab PDF respectively.

- [ ] **Step 1: Write failing formula-presentation tests**

Assert every `core_formulas(document)` item has non-empty `evaluated_latex` and `evaluated_html`, Streamlit calls `st.latex` for symbolic and evaluated equations, HTML contains a `Numerical result` equation without symbol tables, and PDF text excludes `Variables:` and `Evaluated:` tutorial labels.

```python
self.assertTrue(all(formula.evaluated_latex for formula in core_formulas(document)))
self.assertTrue(all(formula.evaluated_html for formula in core_formulas(document)))
self.assertNotIn("Variables:", report_text)
self.assertNotIn("Evaluated:", report_text)
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run the report, app, HTML, and PDF test modules. Expected: failures because `evaluated_latex` does not exist and current renderers still output evaluated captions and variable definitions.

- [ ] **Step 3: Add evaluated LaTeX for the four core formulas**

Add `evaluated_latex: str = ""` and `evaluated_html: str = ""` to `FormulaDefinition` and populate the four core calculations with compact substitutions, for example:

```python
rf"F_u=(3.500\,\mathrm{{kg}})(0.040\,\mathrm{{m}})"
rf"(6.283\,\mathrm{{rad/s}})^2=5.527\,\mathrm{{N}}"
```

The unbalanced-load numerical equation reports force magnitude and the phase-component relationship without a long `t = 0` prose chain.

- [ ] **Step 4: Render paired equations consistently**

- Streamlit: call `_render_latex(formula.latex)` and `_render_latex(formula.evaluated_latex)`; remove the evaluated and variable captions.
- Offline HTML: render symbolic and numerical equation boxes; remove symbol-definition tables from the concise report.
- PDF: render `formula.html` and `formula.evaluated_html` with the same equation style; remove variable paragraphs.

- [ ] **Step 5: Run focused tests and commit Task 2**

```powershell
python -m unittest tests.test_cyclewash_technical_report tests.test_cyclewash_technical_evaluation_app tests.test_cyclewash_technical_report_html tests.test_cyclewash_technical_report_pdf -v
git add cyclewash_technical_report.py cyclewash_technical_evaluation_app.py cyclewash_technical_report_html.py cyclewash_technical_report_pdf.py tests
git commit -m "refactor: condense technical equations"
```

---

### Task 3: PDF Raster Repair And Cache Versioning

**Files:**
- Modify: `cyclewash_technical_report_pdf.py`
- Modify: `cyclewash_technical_evaluation_app.py`
- Test: `tests/test_cyclewash_technical_report_pdf.py`
- Test: `tests/test_cyclewash_technical_evaluation_app.py`

**Interfaces:**
- Produces: `pdf_report_fingerprint() -> str` and `_cached_pdf_bytes(selected_name, fea_root, stl_root, report_fingerprint) -> bytes`.
- Consumes: normalized STL parts and the existing deterministic ReportLab document.

- [ ] **Step 1: Write failing raster/cache tests**

Assert the PDF fingerprint changes with its explicit schema version, `_cached_pdf_bytes` accepts the fingerprint, and changing only that fingerprint rebuilds cached bytes. Add a deterministic image-quality contract checking that the assembly raster has adequate non-background pixel coverage and several distinct component-color populations.

- [ ] **Step 2: Run focused tests and confirm RED**

Expected: signature/fingerprint tests fail and current white-outline raster does not meet the image-coverage contract.

- [ ] **Step 3: Implement depth-sorted STL rasterization**

Project vertices with an orthographic camera matching the interactive viewer (`Z` up, camera from positive X/negative Y), compute face depth, sample a bounded deterministic set of triangles, and paint far-to-near without white outlines. Use translucent gray enclosure, green drum, red shaft, and amber gear fills.

- [ ] **Step 4: Implement PDF cache fingerprinting**

Hash an explicit `PDF_REPORT_SCHEMA_VERSION` plus `cyclewash_technical_report_pdf.py` bytes. Require the fingerprint in `_cached_pdf_bytes` and pass it from `main()` through `_render_downloads`.

- [ ] **Step 5: Render and inspect both PDF pages**

Generate the Normal report under `tmp/pdfs/`, render at 140 DPI with Poppler, and inspect both PNGs. Required visual result: recognizable assembled washer, legible paired equations, exactly two pages, no clipping, no dense symbol definitions, and one limitations sentence.

- [ ] **Step 6: Run focused tests and commit Task 3**

```powershell
python -m unittest tests.test_cyclewash_technical_report_pdf tests.test_cyclewash_technical_evaluation_app -v
git add cyclewash_technical_report_pdf.py cyclewash_technical_evaluation_app.py tests/test_cyclewash_technical_report_pdf.py tests/test_cyclewash_technical_evaluation_app.py
git commit -m "fix: rebuild concise PDF exports"
```

---

### Task 4: End-To-End Verification And Publication

**Files:**
- Verify all modified production and test files.

- [ ] **Step 1: Run the complete test suite**

```powershell
python -m unittest discover -s tests -v
```

Expected: all 66 existing tests plus new regression tests pass; no failures.

- [ ] **Step 2: Browser-test the local Technical Evaluation**

Open the local app, select Gentle/Normal/Heavy, scrub phase to 0/90/180/270 degrees, and verify shaft/gear stress and drum pressure legends update with finite MPa/kPa values. Confirm no startup error, console error, external request, or blank canvas.

- [ ] **Step 3: Request focused code review**

Review the complete diff for numerical consistency, cache invalidation, PDF determinism, browser performance, and regression coverage. Resolve all Critical and Important findings.

- [ ] **Step 4: Merge and publish**

Fast-forward the verified branch into `main`, rerun the complete suite on merged `main`, push `origin main`, then remove the temporary worktree and merged feature branch.
