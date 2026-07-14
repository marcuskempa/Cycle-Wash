# CycleWash Technical Evaluation Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third Streamlit page with fixed operating scenarios, unbalanced-laundry calculations, analytical animation, and matching PDF/offline HTML technical reports.

**Architecture:** A pure scenario module extends the existing analytical model without changing the FEA request schema. A shared immutable report document owns all values, equations, symbol definitions, provenance, and explanations. The Streamlit page, self-contained HTML exporter, and ReportLab PDF renderer consume that document without recalculation.

**Tech Stack:** Python 3.12, NumPy, Streamlit, Plotly/Three.js assets already bundled in the repository, Pillow, ReportLab, unittest, Streamlit AppTest.

## Global Constraints

- Gentle is fixed at 45 RPM, 100 W, 25% fill, 2.0 kg wet laundry, 25 mm eccentricity, and transient factor 1.5.
- Normal is fixed at 60 RPM, 150 W, 35% fill, 3.5 kg wet laundry, 40 mm eccentricity, and transient factor 2.0.
- Heavy is fixed at 50 RPM, 180 W, 45% fill, 5.0 kg wet laundry, 60 mm eccentricity, and transient factor 2.5.
- Do not add imbalance fields to `EngineeringInputs`; current cached FEA hashes must remain valid.
- Every formula includes display markup, evaluated substitution, complete symbol/unit/source definitions, and a plain-language CycleWash explanation.
- HTML is one offline file with no external HTTP resources.
- Animated colors are labeled `Relative analytical load`, never solved FEA stress.
- Exact cache match is labeled `Solved Stage 1 FEA`; otherwise use `Analytical load estimate`.
- Report generation never launches an FEA solve.
- PDF and HTML consume the same immutable report document.

---

### Task 1: Scenario And Shared Report Model

**Files:**
- Create: `cyclewash_scenarios.py`
- Create: `cyclewash_technical_report.py`
- Create: `tests/test_cyclewash_scenarios.py`
- Create: `tests/test_cyclewash_technical_report.py`

**Interfaces:**
- Produces: `OperatingScenario`, `ScenarioResults`, `SCENARIOS`, `scenario_by_name(name)`, `calculate_scenario(scenario)`, `FormulaDefinition`, `ReportDocument`, and `build_report_document(selected_name, fea_root)`.
- Consumes: `EngineeringInputs`, `AnalyticalResults`, `calculate_engineering_loads`, drivetrain calculator functions, and current dimension constants.

- [ ] **Step 1: Write failing scenario tests**

Test exact preset constants, `F_u = m_u e omega^2`, rotating `F_y/F_z`, added bending moment, combined von Mises stress, and factor of safety.

- [ ] **Step 2: Run scenario tests and verify RED**

Run: `python -m unittest tests.test_cyclewash_scenarios -v`

Expected: import failure because `cyclewash_scenarios` does not exist.

- [ ] **Step 3: Implement immutable scenario calculations**

Use frozen dataclasses. Construct base `EngineeringInputs` with only existing fields and keep imbalance values in `OperatingScenario`. Calculate supplemental bending as:

```python
imbalance_force_n = laundry_mass_kg * eccentricity_m * analytical.angular_speed_rad_s**2
imbalance_moment_n_m = imbalance_force_n * inputs.shaft_reaction_overhang_m
total_moment_n_m = analytical.shaft_bending_moment_n_m + imbalance_moment_n_m
bending_stress_pa = 32.0 * total_moment_n_m / (math.pi * inputs.shaft_diameter_m**3)
von_mises_pa = math.sqrt(bending_stress_pa**2 + 3.0 * analytical.shaft_torsional_shear_pa**2)
factor_of_safety = inputs.shaft_material.yield_strength_pa / von_mises_pa
```

- [ ] **Step 4: Run scenario tests and verify GREEN**

Run: `python -m unittest tests.test_cyclewash_scenarios -v`

Expected: all tests pass.

- [ ] **Step 5: Write failing shared-report tests**

Require three scenario sections, selected scenario detail, drivetrain results, formula identifiers, complete symbol definitions, evaluated values, SI units, provenance labels, assumptions, limitations, and conclusions.

- [ ] **Step 6: Implement the shared immutable report document**

Each `FormulaDefinition` stores `identifier`, `title`, `latex`, `html`, `evaluated`, `symbols`, and `explanation`. Each symbol stores `symbol`, `meaning`, `unit`, and `source`. The report builder computes once and returns only frozen tuples/dataclasses.

- [ ] **Step 7: Run Task 1 tests**

Run: `python -m unittest tests.test_cyclewash_scenarios tests.test_cyclewash_technical_report -v`

Expected: all tests pass.

- [ ] **Step 8: Commit Task 1**

```powershell
git add cyclewash_scenarios.py cyclewash_technical_report.py tests
git commit -m "feat: add technical evaluation model"
```

### Task 2: Single-File Offline HTML Report

**Files:**
- Create: `cyclewash_technical_report_html.py`
- Create: `cyclewash_technical_report_template.html`
- Create: `tests/test_cyclewash_technical_report_html.py`
- Reuse: `assets/cyclewash-three-bundle.min.js`
- Reuse: `cyclewash_structural_visualizer.py`
- Reuse: `cyclewash_geometry_policy.py`

**Interfaces:**
- Consumes: `ReportDocument` and STL files in the application root.
- Produces: `build_offline_report_html(document, stl_root) -> bytes`.

- [ ] **Step 1: Write failing HTML contract tests**

Require a UTF-8 HTML document containing all three scenarios, report sections, formula definitions, embedded Three.js, embedded geometry, scenario buttons, play/pause, phase slider, speed selector, water/laundry objects, provenance text, and no `http://`, `https://`, `<script src=`, or `<link href=` references.

- [ ] **Step 2: Run HTML tests and verify RED**

Run: `python -m unittest tests.test_cyclewash_technical_report_html -v`

- [ ] **Step 3: Implement compact embedded geometry**

Load enclosure, door, inner drum, gear, and shaft STLs. Apply the authoritative assembly transforms. Deterministically reduce display triangles when needed, serialize float arrays compactly, and embed the JSON directly in the HTML.

- [ ] **Step 4: Implement the offline animation runtime**

Rotate drum, shaft, and gear together around Blender X. Keep the door closed and 50% transparent. Keep water gravity-referenced with bounded sinusoidal slosh. Move a laundry mass at the scenario eccentricity and show a rotating force arrow. Map the current phase to relative analytical load colors and expose scenario/playback controls.

- [ ] **Step 5: Render report formulas and explanations**

Use semantic HTML/CSS equation blocks, `<sub>`, `<sup>`, Unicode Greek symbols, symbol tables, scenario comparison, assumptions, limitations, and conclusion. Include print CSS so the same HTML can be printed cleanly.

- [ ] **Step 6: Run HTML tests**

Run: `python -m unittest tests.test_cyclewash_technical_report_html -v`

- [ ] **Step 7: Commit Task 2**

```powershell
git add cyclewash_technical_report_html.py cyclewash_technical_report_template.html tests/test_cyclewash_technical_report_html.py
git commit -m "feat: export offline technical evaluation"
```

### Task 3: Printable PDF Report

**Files:**
- Create: `cyclewash_technical_report_pdf.py`
- Create: `tests/test_cyclewash_technical_report_pdf.py`
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: `ReportDocument`, shared STL geometry, and ReportLab.
- Produces: `build_report_pdf(document, stl_root) -> bytes`.

- [ ] **Step 1: Add ReportLab dependency and write failing PDF tests**

Add `reportlab>=4.2`. Require `%PDF` signature, multiple pages, scenario names, formula titles, units, provenance, assumptions, limitations, and conclusion. Extract text with ReportLab/PDF utilities available to the test environment.

- [ ] **Step 2: Run PDF tests and verify RED**

Run: `python -m unittest tests.test_cyclewash_technical_report_pdf -v`

- [ ] **Step 3: Implement the report styles and page templates**

Use ReportLab Platypus with repeating headers, page numbers, bounded tables, paragraph styles, equation blocks, `KeepTogether`, and Unicode-capable registered fonts where available.

- [ ] **Step 4: Implement static technical illustrations**

Use Pillow and NumPy to render deterministic isometric assembly/load diagrams from simplified STL geometry. Include a scenario comparison chart and an imbalance-force schematic. Raster rendering is display-only and must not recalculate engineering values.

- [ ] **Step 5: Render all report sections from the shared document**

Include executive summary, geometry/drivetrain, scenario comparison, detailed selected scenario, formula catalogue, optional exact FEA section, assumptions, limitations, interpretation, and conclusion.

- [ ] **Step 6: Run PDF and shared-document tests**

Run: `python -m unittest tests.test_cyclewash_technical_report tests.test_cyclewash_technical_report_pdf -v`

- [ ] **Step 7: Commit Task 3**

```powershell
git add cyclewash_technical_report_pdf.py requirements.txt tests/test_cyclewash_technical_report_pdf.py
git commit -m "feat: export printable engineering report"
```

### Task 4: Third Streamlit Page And Integration

**Files:**
- Create: `cyclewash_technical_evaluation_app.py`
- Create: `pages/3_Technical_Evaluation.py`
- Create: `tests/test_cyclewash_technical_evaluation_app.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: scenario/report model and both exporters.
- Produces: `main()` for the third Streamlit page.

- [ ] **Step 1: Write failing page source and AppTest tests**

Require page title, three fixed scenario options, viewer, play/pause/phase/speed controls, scenario metrics, comparison table, formula sections using `st.latex`, analytical provenance, and two download buttons.

- [ ] **Step 2: Run page tests and verify RED**

Run: `python -m unittest tests.test_cyclewash_technical_evaluation_app -v`

- [ ] **Step 3: Implement the report page**

Use a quiet work-focused layout. Place the large dark 3D viewer beside selected-scenario metrics and equations. Use tabs for `Presentation` and `Technical Report`, avoiding nested cards. Cache STL parsing and export bytes with `st.cache_data`.

- [ ] **Step 4: Add export controls and README instructions**

Expose `Download PDF Report` and `Download Offline HTML`. Document `Gear_Builder.py` as the Streamlit entrypoint and describe the three pages.

- [ ] **Step 5: Run AppTest on all three pages**

Run AppTest for `Gear_Builder.py`, `pages/2_Structural_Load_Visualizer.py`, and `pages/3_Technical_Evaluation.py` with zero exceptions.

- [ ] **Step 6: Run the complete feature suite**

Run: `python -m unittest discover -s tests -p "test_cyclewash_*.py"`

- [ ] **Step 7: Commit Task 4**

```powershell
git add cyclewash_technical_evaluation_app.py pages/3_Technical_Evaluation.py README.md tests/test_cyclewash_technical_evaluation_app.py
git commit -m "feat: add technical evaluation page"
```

### Task 5: Visual And Deployment Verification

**Files:**
- Verify: all files changed by Tasks 1-4.

**Interfaces:**
- Produces: reviewed, deployable feature branch.

- [ ] Compile all Python modules.
- [ ] Run all unit tests and three-page AppTest checks.
- [ ] Start Streamlit and inspect desktop and mobile screenshots for overlap, clipping, missing geometry, and unreadable formulas.
- [ ] Export PDF and HTML, verify both are nonempty, and open the HTML offline.
- [ ] Confirm HTML contains no network dependencies.
- [ ] Confirm `requirements.txt` is sufficient for Streamlit Community Cloud.
- [ ] Run final code review and resolve all critical or important findings.
