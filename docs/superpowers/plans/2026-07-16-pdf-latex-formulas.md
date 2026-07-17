# CycleWash PDF LaTeX Formulas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the four PDF calculation blocks with reliable LaTeX-style notation and add one selectable gray calculator expression beneath each block.

**Architecture:** `cyclewash_technical_report.py` remains the authoritative formula and numerical-value source and gains an explicit calculator-expression field. `cyclewash_technical_report_pdf.py` converts the existing LaTeX strings to in-memory PNGs with Matplotlib MathText, embeds them in compact ReportLab panels, and leaves the calculator line as selectable PDF text. The PDF cache schema advances to v3 so Streamlit cannot reuse the previous plain-text export.

**Tech Stack:** Python 3.12+, Matplotlib MathText, ReportLab, Pillow, NumPy, Streamlit cache fingerprints, `unittest`, Poppler `pdftoppm`.

## Global Constraints

- Keep the Technical Evaluation report exactly two A4 pages.
- Preserve all existing engineering inputs, equations, numerical results, scenario comparisons, STL imagery, and limitations text.
- Use `matplotlib>=3.8`; do not require a system LaTeX installation.
- Render `FormulaDefinition.latex` and `FormulaDefinition.evaluated_latex` as proportional, unclipped images.
- Keep each calculator expression as selectable ASCII PDF text using `*`, `/`, `^`, `sqrt`, and `pi`.
- Put units only after calculated results in calculator lines.
- Fail PDF generation on missing LaTeX, missing calculator expressions, or MathText parse errors.
- Bump `PDF_REPORT_SCHEMA_VERSION` to `cyclewash-pdf-v3`.

---

### Task 1: Add Calculator Expressions To The Formula Model

**Files:**
- Modify: `cyclewash_technical_report.py:52-63, 285-545`
- Test: `tests/test_cyclewash_technical_report.py`

**Interfaces:**
- Consumes: existing `FormulaDefinition`, `ReportDocument`, `core_formulas(document)` and calculated scenario values.
- Produces: `FormulaDefinition.calculator_expression: str` populated for every formula returned by `core_formulas(document)`.

- [ ] **Step 1: Write the failing calculator-expression test**

Add this test to `CycleWashTechnicalReportTests`:

```python
def test_core_formulas_include_ascii_calculator_expressions(self) -> None:
    document = build_report_document("Normal", PROJECT_ROOT / "fea_results")
    expressions = {
        formula.identifier: formula.calculator_expression
        for formula in core_formulas(document)
    }

    self.assertEqual(
        {
            "drivetrain_speed_ratio": (
                "60.0*34/32 = 63.750 RPM; "
                "34*60.0/60.000 = 34.000 teeth"
            ),
            "angular_speed_and_edge_velocity": (
                "2*pi*60.000/60 = 6.2832 rad/s; "
                "6.2832*0.270 = 1.6965 m/s"
            ),
            "unbalanced_wet_laundry_load": (
                "3.500*0.040*6.2832^2 = 5.527 N"
            ),
            "combined_stress_and_factor_of_safety": (
                "sqrt(33.884^2 + 3*15.563^2) = 43.298 MPa; "
                "250.000/43.298 = 5.774"
            ),
        },
        expressions,
    )
    for expression in expressions.values():
        self.assertTrue(expression)
        self.assertIsNone(re.search(r"[×÷√π]", expression))
```

Add `import re` if it is not already present.

- [ ] **Step 2: Run the test to verify RED**

Run:

```powershell
python -m unittest tests.test_cyclewash_technical_report.CycleWashTechnicalReportTests.test_core_formulas_include_ascii_calculator_expressions -v
```

Expected: `ERROR` with `AttributeError: 'FormulaDefinition' object has no attribute 'calculator_expression'`.

- [ ] **Step 3: Extend `FormulaDefinition`**

Append the optional field so existing positional constructors remain compatible:

```python
@dataclass(frozen=True)
class FormulaDefinition:
    """One explanatory equation with accessible offline display content."""

    identifier: str
    title: str
    latex: str
    html: str
    evaluated: str
    symbols: tuple[SymbolDefinition, ...]
    explanation: str
    evaluated_latex: str = ""
    evaluated_html: str = ""
    calculator_expression: str = ""
```

- [ ] **Step 4: Populate the four core formula expressions**

Add these keyword arguments to their corresponding `FormulaDefinition` constructors:

```python
calculator_expression=(
    f"{drivetrain.pedal_rpm:.1f}*{drivetrain.front_teeth:d}/"
    f"{drivetrain.practical_rear_teeth:d} = {drivetrain.actual_drum_rpm:.3f} RPM; "
    f"{drivetrain.front_teeth:d}*{drivetrain.pedal_rpm:.1f}/"
    f"{scenario.speed_rpm:.3f} = {drivetrain.exact_rear_teeth:.3f} teeth"
),
```

```python
calculator_expression=(
    f"2*pi*{scenario.speed_rpm:.3f}/60 = {analytical.angular_speed_rad_s:.4f} rad/s; "
    f"{analytical.angular_speed_rad_s:.4f}*{inputs.drum_radius_m:.3f} = "
    f"{analytical.angular_speed_rad_s * inputs.drum_radius_m:.4f} m/s"
),
```

```python
calculator_expression=(
    f"{scenario.laundry_mass_kg:.3f}*{scenario.eccentricity_m:.3f}*"
    f"{analytical.angular_speed_rad_s:.4f}^2 = {results.imbalance_force_n:.3f} N"
),
```

```python
calculator_expression=(
    f"sqrt({results.bending_stress_pa / 1.0e6:.3f}^2 + "
    f"3*{analytical.shaft_torsional_shear_pa / 1.0e6:.3f}^2) = "
    f"{results.von_mises_pa / 1.0e6:.3f} MPa; "
    f"{inputs.shaft_material.yield_strength_pa / 1.0e6:.3f}/"
    f"{results.von_mises_pa / 1.0e6:.3f} = {results.factor_of_safety:.3f}"
),
```

- [ ] **Step 5: Run the report-model tests to verify GREEN**

Run:

```powershell
python -m unittest tests.test_cyclewash_technical_report -v
```

Expected: all `CycleWashTechnicalReportTests` pass.

- [ ] **Step 6: Commit the formula-model change**

```powershell
git add cyclewash_technical_report.py tests/test_cyclewash_technical_report.py
git commit -m "feat: add PDF calculator expressions"
```

---

### Task 2: Add A Deterministic MathText Renderer

**Files:**
- Modify: `requirements.txt`
- Modify: `cyclewash_technical_report_pdf.py:1-35, 360-380`
- Test: `tests/test_cyclewash_technical_report_pdf.py`

**Interfaces:**
- Consumes: a non-empty MathText-compatible LaTeX string and a font size in points.
- Produces: `_latex_formula_png(latex: str, font_size: float = 10.5) -> bytes` and `_latex_image_flowable(latex: str, max_width: float, max_height: float, font_size: float) -> Image`.

- [ ] **Step 1: Add and install the rendering dependency**

Append to `requirements.txt`:

```text
matplotlib>=3.8
```

Install the project requirements:

```powershell
python -m pip install -r requirements.txt
```

Expected: installation completes and `python -c "import matplotlib"` exits with code 0.

- [ ] **Step 2: Write failing MathText renderer tests**

Import `_latex_formula_png` and add:

```python
def test_latex_formula_png_is_deterministic_and_visible(self) -> None:
    latex = r"\sigma_{vm}=\sqrt{\sigma_b^2+3\tau_t^2}"

    first = _latex_formula_png(latex)
    second = _latex_formula_png(latex)
    pixels = np.asarray(Image.open(BytesIO(first)).convert("RGBA"))

    self.assertEqual(first, second)
    self.assertGreater(pixels.shape[0], 10)
    self.assertGreater(pixels.shape[1], 100)
    self.assertGreater((pixels[:, :, 3] > 0).sum(), 1_000)

def test_latex_formula_png_rejects_empty_or_invalid_math(self) -> None:
    with self.assertRaisesRegex(ValueError, "must not be empty"):
        _latex_formula_png("   ")
    with self.assertRaisesRegex(ValueError, "unable to render PDF equation"):
        _latex_formula_png(r"\definitely_not_a_mathtext_command")
```

- [ ] **Step 3: Run the tests to verify RED**

Run:

```powershell
python -m unittest \
  tests.test_cyclewash_technical_report_pdf.CycleWashTechnicalReportPdfTests.test_latex_formula_png_is_deterministic_and_visible \
  tests.test_cyclewash_technical_report_pdf.CycleWashTechnicalReportPdfTests.test_latex_formula_png_rejects_empty_or_invalid_math -v
```

Expected: `ImportError` because `_latex_formula_png` does not exist.

- [ ] **Step 4: Implement the MathText renderer**

Add imports:

```python
import matplotlib
from matplotlib.font_manager import FontProperties
from matplotlib.mathtext import math_to_image
```

Add the renderer near `_image_flowable`:

```python
MATH_DPI = 180.0


def _latex_formula_png(latex: str, font_size: float = 10.5) -> bytes:
    expression = latex.strip()
    if not expression:
        raise ValueError("PDF equation LaTeX must not be empty")
    output = BytesIO()
    try:
        with matplotlib.rc_context(
            {
                "mathtext.fontset": "dejavuserif",
                "savefig.transparent": True,
            }
        ):
            math_to_image(
                f"${expression}$",
                output,
                prop=FontProperties(family="DejaVu Serif", size=font_size),
                dpi=MATH_DPI,
                format="png",
                color="#123047",
            )
    except (RuntimeError, ValueError) as error:
        raise ValueError("unable to render PDF equation with MathText") from error
    png = output.getvalue()
    with PillowImage.open(BytesIO(png)) as rendered:
        if rendered.width <= 1 or rendered.height <= 1:
            raise ValueError("unable to render PDF equation with MathText")
    return png


def _latex_image_flowable(
    latex: str,
    max_width: float,
    max_height: float,
    font_size: float,
) -> Image:
    png = _latex_formula_png(latex, font_size)
    with PillowImage.open(BytesIO(png)) as rendered:
        natural_width = rendered.width * 72.0 / MATH_DPI
        natural_height = rendered.height * 72.0 / MATH_DPI
    scale = min(1.0, max_width / natural_width, max_height / natural_height)
    return Image(
        BytesIO(png),
        width=natural_width * scale,
        height=natural_height * scale,
        hAlign="CENTER",
    )
```

This follows the official `math_to_image(s, filename_or_obj, prop, dpi, format, color=...)` API; the expression is enclosed in dollar signs as required by the Matplotlib documentation.

- [ ] **Step 5: Run the renderer tests to verify GREEN**

Run the two tests from Step 3 again.

Expected: both tests pass with deterministic visible PNG output.

- [ ] **Step 6: Commit the renderer**

```powershell
git add requirements.txt cyclewash_technical_report_pdf.py tests/test_cyclewash_technical_report_pdf.py
git commit -m "feat: render PDF equations with MathText"
```

---

### Task 3: Integrate Typeset Panels And Selectable Calculator Text

**Files:**
- Modify: `cyclewash_technical_report_pdf.py:47-55, 105-175, 300-380`
- Test: `tests/test_cyclewash_technical_report_pdf.py`

**Interfaces:**
- Consumes: `FormulaDefinition.latex`, `evaluated_latex`, and `calculator_expression`.
- Produces: `_compact_formula_block(formula: FormulaDefinition, styles: dict[str, ParagraphStyle]) -> Flowable` containing two equation images and one selectable calculator line.

- [ ] **Step 1: Write failing PDF integration assertions**

Update `test_builds_exact_two_page_introductory_report`:

```python
self.assertEqual(2, len(re.findall(rb"/Type\s*/Page\b", pdf_bytes)))
self.assertEqual(9, len(re.findall(rb"/Subtype\s*/Image\b", pdf_bytes)))
for formula in core_formulas(self.document):
    self.assertIn(formula.title, report_text)
    self.assertIn(formula.calculator_expression, report_text)
self.assertNotIn("_equation_markup", source)
```

Update `test_exported_pdf_bytes_and_embedded_images_are_deterministic` to expect nine images.

Replace the old equation-style test with:

```python
def test_calculator_style_is_small_gray_monospaced_text(self) -> None:
    style = _report_styles()["calculator"]

    self.assertEqual("Courier", style.fontName)
    self.assertLessEqual(style.fontSize, 7.0)
    self.assertEqual(colors.HexColor("#66737D"), style.textColor)
    self.assertEqual(TA_CENTER, style.alignment)
```

Import `TA_CENTER` and `colors` from ReportLab in the test module.

- [ ] **Step 2: Run the integration tests to verify RED**

Run:

```powershell
python -m unittest \
  tests.test_cyclewash_technical_report_pdf.CycleWashTechnicalReportPdfTests.test_builds_exact_two_page_introductory_report \
  tests.test_cyclewash_technical_report_pdf.CycleWashTechnicalReportPdfTests.test_exported_pdf_bytes_and_embedded_images_are_deterministic \
  tests.test_cyclewash_technical_report_pdf.CycleWashTechnicalReportPdfTests.test_calculator_style_is_small_gray_monospaced_text -v
```

Expected: failures because the PDF still contains one image, calculator expressions are absent from extracted PDF text, and the calculator style is undefined.

- [ ] **Step 3: Replace the equation style with the calculator style**

Remove `_register_equation_font()`, its call, the `pdfmetrics` and `TTFont` imports, the `equation` style, and `_equation_markup()`.

Add this style to `_report_styles()`:

```python
"calculator": ParagraphStyle(
    "CycleWashCalculator",
    parent=styles["BodyText"],
    fontName="Courier",
    fontSize=6.5,
    leading=8.0,
    textColor=colors.HexColor("#66737D"),
    alignment=TA_CENTER,
),
```

- [ ] **Step 4: Replace `_compact_formula_block` with a typeset panel**

Import `escape` from `html` and implement:

```python
def _compact_formula_block(
    formula: FormulaDefinition,
    styles: dict[str, ParagraphStyle],
) -> Flowable:
    if not formula.latex.strip() or not formula.evaluated_latex.strip():
        raise ValueError(f"core PDF formula is missing LaTeX: {formula.identifier}")
    if not formula.calculator_expression.strip():
        raise ValueError(
            f"core PDF formula is missing calculator expression: {formula.identifier}"
        )
    panel = Table(
        [
            [
                _latex_image_flowable(
                    formula.latex,
                    CONTENT_WIDTH - 18,
                    24,
                    10.5,
                )
            ],
            [
                _latex_image_flowable(
                    formula.evaluated_latex,
                    CONTENT_WIDTH - 18,
                    30,
                    10.0,
                )
            ],
            [
                Paragraph(
                    escape(formula.calculator_expression),
                    styles["calculator"],
                )
            ],
        ],
        colWidths=[CONTENT_WIDTH],
        hAlign="LEFT",
    )
    panel.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALE_BLUE),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#B9CBD7")),
                ("LINEABOVE", (0, 1), (-1, 1), 0.35, colors.HexColor("#C4D2DA")),
                ("LINEABOVE", (0, 2), (-1, 2), 0.35, colors.HexColor("#C4D2DA")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return KeepTogether(
        [
            Paragraph(formula.title, styles["subsection"]),
            panel,
        ]
    )
```

- [ ] **Step 5: Bump the PDF schema**

Change:

```python
PDF_REPORT_SCHEMA_VERSION = "cyclewash-pdf-v3"
```

Update `test_pdf_fingerprint_is_schema_versioned_sha256` to expect v3 and patch v4 when checking fingerprint changes.

- [ ] **Step 6: Run the PDF tests to verify GREEN**

Run:

```powershell
python -m unittest tests.test_cyclewash_technical_report_pdf -v
```

Expected: all PDF tests pass, the report remains two pages, the PDF contains nine images, and all four calculator lines are extractable.

- [ ] **Step 7: Commit the integrated PDF layout**

```powershell
git add cyclewash_technical_report_pdf.py tests/test_cyclewash_technical_report_pdf.py
git commit -m "feat: typeset PDF calculation blocks"
```

---

### Task 4: Render, Inspect, And Release The Updated Report

**Files:**
- Verify: `cyclewash_technical_report_pdf.py`
- Verify: `cyclewash_technical_report.py`
- Verify: `requirements.txt`
- Verify: `tests/`

**Interfaces:**
- Consumes: the completed report model, MathText renderer, ReportLab panel, and v3 PDF fingerprint.
- Produces: a visually verified two-page PDF and a pushed `main` branch for Streamlit Cloud deployment.

- [ ] **Step 1: Generate a fresh Normal-scenario PDF**

Use the project Python environment:

```powershell
New-Item -ItemType Directory -Force -Path tmp\pdfs | Out-Null
python -c "from pathlib import Path; from cyclewash_technical_report import build_report_document; from cyclewash_technical_report_pdf import build_report_pdf; root=Path('.'); Path('tmp/pdfs/cyclewash-latex-report.pdf').write_bytes(build_report_pdf(build_report_document('Normal', root/'fea_results'), root))"
```

Expected: `tmp/pdfs/cyclewash-latex-report.pdf` is created without warnings or exceptions.

- [ ] **Step 2: Render both pages to PNG**

```powershell
pdftoppm -png -r 150 tmp\pdfs\cyclewash-latex-report.pdf tmp\pdfs\cyclewash-latex-page
```

Expected: `cyclewash-latex-page-1.png` and `cyclewash-latex-page-2.png` are created.

- [ ] **Step 3: Visually inspect both pages**

Confirm all of the following:

- Page 1 assembly, metrics, and scenario table are unchanged and legible.
- Page 2 has four green calculation headings.
- Every symbolic and evaluated equation has Greek symbols, radicals, superscripts, and subscripts with no missing-glyph boxes.
- Every calculator line is small, gray, centered, and remains readable.
- No equation or calculator line is clipped, stretched, overlapping, or split across pages.
- The conclusion and single limitations statement remain on page 2.

- [ ] **Step 4: Run the complete test suite**

```powershell
python -m unittest discover -s tests -v
```

Expected: all 78 existing tests plus the new formula and MathText tests pass with zero failures or errors.

- [ ] **Step 5: Check repository cleanliness and diff quality**

```powershell
git diff --check
git status --short --branch
```

Expected: `git diff --check` prints nothing; only intended source, requirement, and test files are changed. Remove `tmp/pdfs/` before committing any remaining verification-only changes.

- [ ] **Step 6: Commit any final test-only adjustment**

If visual verification required a test threshold or fixed dimension adjustment, commit only those reviewed changes:

```powershell
git add requirements.txt cyclewash_technical_report.py cyclewash_technical_report_pdf.py tests/test_cyclewash_technical_report.py tests/test_cyclewash_technical_report_pdf.py
git commit -m "test: verify PDF LaTeX report layout"
```

If there are no remaining changes, skip this commit.

- [ ] **Step 7: Push `main` and verify synchronization**

```powershell
git push origin main
git rev-parse HEAD
git rev-parse origin/main
```

Expected: the two commit hashes match and Streamlit Cloud begins redeploying the v3 PDF cache schema.

## Reference

- Matplotlib stable MathText API: `https://matplotlib.org/stable/api/mathtext_api.html`
- Approved design: `docs/superpowers/specs/2026-07-16-pdf-latex-formulas-design.md`
