"""Streamlit technical-evaluation page for the fixed CycleWash scenarios."""

from __future__ import annotations

from html import escape
from pathlib import Path
import re

try:
    import streamlit as st
except ImportError as error:
    missing_name = getattr(error, "name", "streamlit")
    raise SystemExit(
        f"Missing dependency: {missing_name}. Install GUI dependencies with: "
        "python -m pip install -r requirements.txt"
    ) from error

from cyclewash_scenarios import SCENARIOS
from cyclewash_technical_report import (
    LIMITATIONS_NOTE,
    FormulaDefinition,
    ReportDocument,
    build_report_document,
    core_formulas,
)
from cyclewash_technical_report_html import build_offline_report_html, build_scenario_viewer_html
from cyclewash_technical_report_pdf import build_report_pdf


PROJECT_ROOT = Path(__file__).resolve().parent
SCENARIO_NAMES = tuple(scenario.name for scenario in SCENARIOS)
LATEX_DISPLAY_BREAK = re.compile(r",\s*\\(?:qquad|quad)\s*")


@st.cache_data(show_spinner=False)
def _cached_report_document(selected_name: str, fea_root: str) -> ReportDocument:
    """Load the immutable report data and any exact cached FEA summary."""

    return build_report_document(selected_name, fea_root)


@st.cache_data(show_spinner=False)
def _cached_viewer_html(selected_name: str, fea_root: str, stl_root: str) -> str:
    """Cache STL parsing and the generated offline viewer document."""

    document = _cached_report_document(selected_name, fea_root)
    return build_scenario_viewer_html(document, selected_name, stl_root)


@st.cache_data(show_spinner=False)
def _cached_pdf_bytes(selected_name: str, fea_root: str, stl_root: str) -> bytes:
    """Cache printable report bytes for the selected fixed scenario."""

    document = _cached_report_document(selected_name, fea_root)
    return build_report_pdf(document, stl_root)


@st.cache_data(show_spinner=False)
def _cached_html_bytes(selected_name: str, fea_root: str, stl_root: str) -> bytes:
    """Cache the self-contained offline report bytes for the selected scenario."""

    document = _cached_report_document(selected_name, fea_root)
    return build_offline_report_html(document, stl_root)


def _format_stress_mpa(stress_pa: float) -> str:
    return f"{stress_pa / 1.0e6:.2f} MPa"


def _split_latex_displays(latex: str) -> tuple[str, ...]:
    """Split compound equations into concise phone-width display blocks."""

    return tuple(
        display.strip()
        for display in LATEX_DISPLAY_BREAK.split(latex)
        if display.strip()
    )


def _render_latex(latex: str) -> None:
    for display in _split_latex_displays(latex):
        st.latex(display)


def _render_table(headers: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> None:
    """Render responsive report tables without forcing a horizontal viewport."""

    header_html = "".join(f"<th>{escape(header)}</th>" for header in headers)
    rows_html = "".join(
        "<tr>" + "".join(f"<td>{escape(value)}</td>" for value in row) + "</tr>"
        for row in rows
    )
    st.markdown(
        "<style>"
        ".cyclewash-table { width: 100%; border-collapse: collapse; table-layout: fixed; }"
        ".cyclewash-table th, .cyclewash-table td { overflow-wrap: anywhere; vertical-align: top; "
        "padding: 0.45rem; border-bottom: 1px solid rgba(128, 128, 128, 0.28); text-align: left; }"
        ".cyclewash-table th { font-weight: 600; }"
        "</style>"
        f"<table class=\"cyclewash-table\"><thead><tr>{header_html}</tr></thead>"
        f"<tbody>{rows_html}</tbody></table>",
        unsafe_allow_html=True,
    )


def _render_selected_metrics(document: ReportDocument) -> None:
    report = document.selected_report
    results = report.results
    analytical = results.analytical
    metrics = (
        ("Current RPM", f"{report.scenario.speed_rpm:.0f} RPM"),
        ("Water mass", f"{analytical.retained_water_mass_kg:.1f} kg"),
        ("Imbalance force", f"{results.imbalance_force_n:.1f} N"),
        ("Combined shaft stress", _format_stress_mpa(results.von_mises_pa)),
        ("Factor of safety", f"{results.factor_of_safety:.2f}"),
    )
    columns = st.columns(len(metrics))
    for column, (label, value) in zip(columns, metrics, strict=True):
        column.metric(label, value)


def _render_formula(formula: FormulaDefinition) -> None:
    st.subheader(formula.title)
    _render_latex(formula.latex)
    st.caption(formula.evaluated)
    definitions = "; ".join(
        f"{symbol.symbol}: {symbol.meaning} [{symbol.unit}]" for symbol in formula.symbols
    )
    st.caption(f"Variables: {definitions}")


def _scenario_comparison_rows(document: ReportDocument) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            report.scenario.name,
            f"{report.scenario.speed_rpm:.0f} RPM",
            f"{report.results.analytical.retained_water_mass_kg:.1f} kg",
            f"{report.results.imbalance_force_n:.1f} N",
            _format_stress_mpa(report.results.von_mises_pa),
            f"{report.results.factor_of_safety:.2f}",
        )
        for report in document.scenario_reports
    )


def _render_comparison(document: ReportDocument) -> None:
    st.header("Scenario Comparison")
    _render_table(
        (
            "Scenario",
            "RPM",
            "Water mass",
            "Imbalance force",
            "Shaft stress",
            "FoS",
        ),
        _scenario_comparison_rows(document),
    )


def _render_downloads(selected_name: str, fea_root: str, stl_root: str) -> None:
    try:
        pdf_bytes = _cached_pdf_bytes(selected_name, fea_root, stl_root)
        html_bytes = _cached_html_bytes(selected_name, fea_root, stl_root)
    except (OSError, RuntimeError, TypeError, ValueError):
        st.error(
            "Report exports are unavailable. Verify that the local STL files and report assets are present, "
            "then reload this page."
        )
        return

    left, right = st.columns(2)
    with left:
        st.download_button(
            "Download PDF Report",
            data=pdf_bytes,
            file_name=f"cyclewash_{selected_name.lower()}_technical_evaluation.pdf",
            mime="application/pdf",
            type="primary",
        )
    with right:
        st.download_button(
            "Download Offline HTML",
            data=html_bytes,
            file_name=f"cyclewash_{selected_name.lower()}_technical_evaluation.html",
            mime="text/html",
        )


def main() -> None:
    """Render the fixed-scenario CycleWash technical evaluation."""

    st.set_page_config(page_title="CycleWash Technical Evaluation", layout="wide")
    st.title("CycleWash Technical Evaluation")
    selected_name = st.segmented_control(
        "Operating scenario",
        options=SCENARIO_NAMES,
        default="Normal",
        selection_mode="single",
        label_visibility="collapsed",
    )
    if selected_name is None:
        selected_name = "Normal"

    stl_root = str(PROJECT_ROOT)
    fea_root = str(PROJECT_ROOT / "fea_results")
    try:
        document = _cached_report_document(selected_name, fea_root)
        viewer_html = _cached_viewer_html(selected_name, fea_root, stl_root)
    except (OSError, RuntimeError, TypeError, ValueError):
        st.error(
            "Technical evaluation could not load its local report or STL assets. "
            "Confirm the CycleWash project files are complete, then reload the page."
        )
        return

    _render_selected_metrics(document)
    st.iframe(viewer_html, height=610)
    st.header("Core Engineering Checks")
    for formula in core_formulas(document):
        _render_formula(formula)
    _render_comparison(document)
    st.info(LIMITATIONS_NOTE)
    _render_downloads(selected_name, fea_root, stl_root)


if __name__ == "__main__":
    main()
