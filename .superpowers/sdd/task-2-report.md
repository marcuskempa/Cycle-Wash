# Task 2 Report: Concise Symbolic And Evaluated Equations

## Status

Completed.

## Commit

`refactor: condense technical equations`

## Tests

RED:

```text
.\.venv\Scripts\python.exe -m unittest tests.test_cyclewash_technical_report tests.test_cyclewash_technical_evaluation_app tests.test_cyclewash_technical_report_html tests.test_cyclewash_technical_report_pdf
Ran 51 tests in 47.049s
FAILED (errors=7, skipped=1)
```

The failures were the expected missing `FormulaDefinition.evaluated_latex` and
`FormulaDefinition.evaluated_html` fields across the core-formula and renderer
contracts.

GREEN:

```text
.\.venv\Scripts\python.exe -m unittest tests.test_cyclewash_technical_report tests.test_cyclewash_technical_evaluation_app tests.test_cyclewash_technical_report_html tests.test_cyclewash_technical_report_pdf
Ran 51 tests in 45.647s
OK (skipped=1)
```

`git diff --check` also completed with no whitespace errors.

## Files Changed

- `cyclewash_technical_report.py`
- `cyclewash_technical_evaluation_app.py`
- `cyclewash_technical_report_html.py`
- `cyclewash_technical_report_pdf.py`
- `tests/test_cyclewash_technical_report.py`
- `tests/test_cyclewash_technical_evaluation_app.py`
- `tests/test_cyclewash_technical_report_html.py`
- `tests/test_cyclewash_technical_report_pdf.py`

## Self-Review

- Added defaulted evaluated-equation fields after existing constructor fields so
  non-core formula construction remains compatible.
- Populated compact LaTeX and ReportLab-compatible HTML equations only for the
  four core formulas, including units inside numerical substitutions.
- Kept unbalanced-load components symbolic while rendering only the numerical
  magnitude substitution.
- Confirmed Streamlit, offline HTML, and PDF render symbolic plus evaluated
  equations without `Variables:` or `Evaluated:` labels; offline HTML also
  omits symbol lists.
- Confirmed the PDF remains exactly two pages and contains the single approved
  limitations statement.
- Reviewed the diff to preserve the existing Task 1 phase-resolved load-map
  implementation.

## Concerns

- The focused Streamlit tests emit existing bare-runtime `ScriptRunContext`
  warnings; they do not fail the suite.
