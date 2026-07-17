# CycleWash PDF LaTeX Formula Design

## Purpose

Replace the plain-text equation panels on page 2 of the CycleWash Technical Evaluation PDF with reliably typeset mathematical notation. Preserve calculator usability by adding one compact ASCII numeric expression beneath each formula block.

## Approved Scope

This change affects only the four core calculation blocks in the PDF export. It does not change engineering inputs, equations, numerical results, the interactive Streamlit formulas, the STL assembly image, or the two-page report structure.

## Rendering Approach

Use Matplotlib MathText to render `FormulaDefinition.latex` and `FormulaDefinition.evaluated_latex` into transparent PNG images held in memory. MathText provides LaTeX-style Greek symbols, radicals, subscripts, superscripts, and roman units without requiring a system LaTeX installation.

Each calculation block contains:

1. The existing green calculation title.
2. A centered symbolic equation image.
3. A centered evaluated equation image with the selected scenario values and units.
4. One small gray monospaced calculator line.

The two equation images remain inside the existing pale-blue calculation panel. They scale down proportionally when required to stay within the report content width, and they must never be clipped or stretched. The calculator line remains PDF text so it can be selected and copied.

Add `matplotlib>=3.8` to `requirements.txt`. The renderer uses Matplotlib's bundled math fonts, so output remains consistent between Windows development and Streamlit Cloud.

## Calculator Expressions

Extend `FormulaDefinition` with an optional `calculator_expression` string. The four PDF core formulas populate it explicitly using ASCII calculator operators rather than attempting to parse LaTeX or HTML.

The Normal scenario follows these formats, with values generated dynamically from the selected scenario:

```text
60.0*34/32 = 63.750 RPM; 34*60.0/60.000 = 34.000 teeth
2*pi*60.000/60 = 6.2832 rad/s; 6.2832*0.270 = 1.6965 m/s
3.500*0.040*6.2832^2 = 5.527 N
sqrt(33.884^2 + 3*15.563^2) = 43.298 MPa; 250.000/43.298 = 5.774
```

The line is set in a small gray monospaced style below the evaluated equation. It uses `*`, `/`, `^`, `sqrt`, and `pi`, with no explanatory prose. Units appear only after calculated results.

## Data Flow

`core_formulas(document)` remains the authoritative source for symbolic formulas and evaluated values. It also supplies each calculator expression. The PDF renderer receives the immutable `FormulaDefinition`, renders both LaTeX fields to images, and places the calculator string as selectable text.

No numerical value is recalculated in the PDF renderer. Every displayed result continues to come from the existing engineering report model.

## Error Handling

- Missing symbolic or evaluated LaTeX in a core formula raises an actionable PDF-generation error.
- A MathText parse failure stops PDF generation rather than silently falling back to broken glyphs.
- Empty calculator expressions are rejected for the four core PDF formulas.
- Existing Streamlit export error handling continues to show the concise report-assets error message.

## Cache Invalidation

Bump `PDF_REPORT_SCHEMA_VERSION` from `cyclewash-pdf-v2` to `cyclewash-pdf-v3`. The existing PDF fingerprint already includes the PDF renderer and report-model source, so Streamlit will rebuild exports after renderer or calculator-expression changes.

## Verification

- Formula-model tests verify that all four core formulas provide non-empty calculator expressions with ASCII operators.
- PDF renderer tests verify deterministic MathText image output and visible non-background equation pixels.
- PDF text extraction verifies that all four calculator expressions remain selectable text.
- Existing tests continue to require a deterministic PDF, exactly two A4 pages, one embedded assembly image, concise report content, and a single limitations statement.
- Render both final PDF pages to PNG and visually inspect equation clarity, spacing, line wrapping, page count, and the absence of missing-glyph boxes.
- Run the complete CycleWash test suite before committing the implementation.
