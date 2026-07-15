# Task 3 Report: PDF Raster Repair And Cache Versioning

## Status

Completed.

## Commit

`fix: rebuild concise PDF exports`

## Tests

RED:

```text
.\.venv\Scripts\python.exe -m unittest tests.test_cyclewash_technical_report_pdf tests.test_cyclewash_technical_evaluation_app
Ran 17 tests in 4.280s
FAILED (failures=1, errors=2)
```

The expected failures were missing `PDF_REPORT_SCHEMA_VERSION` and
`pdf_report_fingerprint`, plus the missing fourth `_cached_pdf_bytes` cache-key
parameter.

GREEN:

```text
.\.venv\Scripts\python.exe -m unittest tests.test_cyclewash_technical_report_pdf tests.test_cyclewash_technical_evaluation_app
Ran 22 tests in 8.038s
OK
```

`git diff --check` completed with no whitespace errors.

## Files Changed

- `cyclewash_technical_report_pdf.py`
- `cyclewash_technical_evaluation_app.py`
- `tests/test_cyclewash_technical_report_pdf.py`
- `tests/test_cyclewash_technical_evaluation_app.py`
- `.superpowers/sdd/task-3-report.md`

## Rendered Artifacts

- `C:\Users\marcu\Documents\CAD builder\.worktrees\technical-load-report-polish\tmp\pdfs\cyclewash-task-3.pdf`
- `C:\Users\marcu\Documents\CAD builder\.worktrees\technical-load-report-polish\tmp\pdfs\cyclewash-task-3-page-1.png`
- `C:\Users\marcu\Documents\CAD builder\.worktrees\technical-load-report-polish\tmp\pdfs\cyclewash-task-3-page-2.png`

Both pages were rendered at 140 DPI with the bundled Poppler executable and
visually inspected. Page 1 has a legible translucent gray enclosure, visible
green drum, component color key, scenario table, and footer. Page 2 retains
the paired symbolic/evaluated equations, contains the interpretation and
conclusion, and ends with one concise limitations statement. The export remains
exactly two A4 pages.

## Self-Review

- Added a SHA-256 PDF fingerprint over an explicit schema version and the PDF
  renderer source bytes; changing either invalidates the Streamlit PDF cache.
- Passed the fingerprint through `main`, `_render_downloads`, and
  `_cached_pdf_bytes` without changing the Task 2 paired-equation rendering.
- Replaced the isometric mesh rendering with the viewer-aligned +X/-Y,
  Blender-Z-up orthographic projection; face depths are sorted far-to-near.
- Enforced finite XYZ vertices and a non-zero projected span, preserved unified
  assembly proportions, used a deterministic 16,000-triangle-per-part cap,
  and removed white triangle outlines.
- Added deterministic raster quality coverage for non-background area, gray,
  green, red, and amber populations, and the absence of dense white washout.

## Concerns

- Focused Streamlit tests emit existing bare-runtime `ScriptRunContext`
  warnings but pass.
- Poppler reported unavailable `Symbol` and `ArialUnicode` display fonts while
  rasterizing; visual inspection showed the PDF equations and labels rendering
  legibly.

## Review Follow-Up

### RED

```text
.\.venv\Scripts\python.exe -m unittest tests.test_cyclewash_technical_report_pdf tests.test_cyclewash_technical_evaluation_app
Ran 17 tests in 5.135s
FAILED (errors=1)
```

The expected failure was the missing `_paint_triangles_far_to_near` extraction.
The subsequent PDF-only RED run also showed the prior full-raster color test no
longer passed once its gray/green/red/amber checks were correctly scoped to the
schematic crop, and the original equation style's 14-point leading failed the
new 17-point clearance contract.

### GREEN

```text
.\.venv\Scripts\python.exe -m unittest tests.test_cyclewash_technical_report_pdf tests.test_cyclewash_technical_evaluation_app
Ran 24 tests in 6.719s
OK
```

### Additional Artifacts

- `C:\Users\marcu\Documents\CAD builder\.worktrees\technical-load-report-polish\tmp\pdfs\cyclewash-task-3-review.pdf`
- `C:\Users\marcu\Documents\CAD builder\.worktrees\technical-load-report-polish\tmp\pdfs\cyclewash-task-3-review-page-1.png`
- `C:\Users\marcu\Documents\CAD builder\.worktrees\technical-load-report-polish\tmp\pdfs\cyclewash-task-3-review-page-2.png`

Both review-follow-up pages were rendered at 140 DPI and inspected. Page 1 now
contains gray enclosure, green drum, red shaft, and amber gear pixels inside
the assembly crop rather than relying on the legend. Page 2 has visible space
between each green subsection heading and its equation box; subscript descenders
no longer touch the horizontal divider. The report still contains exactly two
A4 pages.

### Review Changes

- Raster assertions now use only the schematic crop for all component-color and
  white-washout checks.
- The fingerprint test compares the result with SHA-256 over the schema version,
  NUL separator, and actual PDF renderer source bytes.
- `_paint_triangles_far_to_near` owns depth ordering and has an overlapping-face
  fixture proving that the nearer triangle wins.
- Equation leading, padding, and subsection clearance were increased; redundant
  formula-block spacer height was removed to retain the two-page layout.
