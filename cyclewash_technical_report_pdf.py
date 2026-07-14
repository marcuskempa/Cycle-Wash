"""Printable ReportLab export for the frozen CycleWash technical-report document."""

from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image as PillowImage
from PIL import ImageDraw, ImageFont
from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch, mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from cyclewash_geometry_policy import normalize_stl_part
from cyclewash_structural_visualizer import StlPartSpec, load_stl_part
from cyclewash_technical_report import FormulaDefinition, ReportDocument, ScenarioReport


PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 16 * mm
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
DARK_BLUE = colors.HexColor("#123047")
GREEN = colors.HexColor("#1A6B57")
RED = colors.HexColor("#B6403B")
PALE_BLUE = colors.HexColor("#EAF1F5")
PALE_GREEN = colors.HexColor("#EAF5F0")
PALE_RED = colors.HexColor("#F8ECEA")

_ASSEMBLY_FILES = (
    ("Enclosure", "enclosure.stl", "#6F8795", "casing"),
    ("Drum", "Inner_Drum.stl", "#1A6B57", "drum"),
    ("Shaft", "shaft.stl", "#B6403B", "shaft"),
    ("Gear", "gear.stl", "#D99B33", "gear"),
)


def build_report_pdf(document: ReportDocument, stl_root: str | Path) -> bytes:
    """Render one printable PDF from a precomputed report document.

    Engineering values are read exclusively from ``document``. STL files provide
    only the display geometry for the labeled assembly schematic.
    """

    if not isinstance(document, ReportDocument):
        raise TypeError("document must be a ReportDocument")
    root = Path(stl_root)
    if not root.is_dir():
        raise ValueError(f"stl_root must be an existing directory: {root}")
    missing_files = [filename for _, filename, _, _ in _ASSEMBLY_FILES if not (root / filename).is_file()]
    if missing_files:
        raise ValueError("stl_root is missing required STL files: " + ", ".join(missing_files))

    rl_config.invariant = 1
    styles = _report_styles()
    output = BytesIO()
    pdf = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        title="CycleWash Technical Evaluation",
        author="CycleWash Engineering",
    )
    story = _report_story(document, root, styles)
    pdf.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return output.getvalue()


def _report_styles() -> dict[str, ParagraphStyle]:
    styles = getSampleStyleSheet()
    _register_equation_font()
    return {
        "title": ParagraphStyle(
            "CycleWashTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=26,
            textColor=DARK_BLUE,
            alignment=TA_CENTER,
            spaceAfter=6,
        ),
        "subtitle": ParagraphStyle(
            "CycleWashSubtitle",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#41596A"),
            alignment=TA_CENTER,
            spaceAfter=14,
        ),
        "section": ParagraphStyle(
            "CycleWashSection",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=16,
            textColor=DARK_BLUE,
            spaceBefore=11,
            spaceAfter=6,
            keepWithNext=True,
        ),
        "subsection": ParagraphStyle(
            "CycleWashSubsection",
            parent=styles["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=10.5,
            leading=13,
            textColor=GREEN,
            spaceBefore=8,
            spaceAfter=4,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "CycleWashBody",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=11.5,
            textColor=colors.HexColor("#1F2933"),
            spaceAfter=5,
        ),
        "small": ParagraphStyle(
            "CycleWashSmall",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=7.1,
            leading=8.8,
            textColor=colors.HexColor("#263642"),
        ),
        "table_header": ParagraphStyle(
            "CycleWashTableHeader",
            parent=styles["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=7.1,
            leading=8.8,
            textColor=colors.white,
        ),
        "equation": ParagraphStyle(
            "CycleWashEquation",
            parent=styles["BodyText"],
            fontName="CycleWashEquation",
            fontSize=10.5,
            leading=14,
            textColor=DARK_BLUE,
            alignment=TA_CENTER,
            borderColor=colors.HexColor("#B9CBD7"),
            borderWidth=0.5,
            borderPadding=6,
            backColor=PALE_BLUE,
            spaceBefore=2,
            spaceAfter=5,
        ),
        "caption": ParagraphStyle(
            "CycleWashCaption",
            parent=styles["BodyText"],
            fontName="Helvetica-Oblique",
            fontSize=7.5,
            leading=9,
            textColor=colors.HexColor("#4A5D69"),
            alignment=TA_CENTER,
            spaceAfter=7,
        ),
    }


def _register_equation_font() -> None:
    if "CycleWashEquation" in pdfmetrics.getRegisteredFontNames():
        return
    import reportlab

    unicode_font = Path(r"C:\Windows\Fonts\segoeui.ttf")
    if not unicode_font.is_file():
        unicode_font = Path(reportlab.__file__).resolve().parent / "fonts" / "Vera.ttf"
    pdfmetrics.registerFont(TTFont("CycleWashEquation", str(unicode_font)))


def _report_story(document: ReportDocument, stl_root: Path, styles: dict[str, ParagraphStyle]) -> list[Flowable]:
    selected = document.selected_report
    story: list[Flowable] = [
        Paragraph("CycleWash Technical Evaluation", styles["title"]),
        Paragraph(
            f"Printable engineering supplement | Generated {date.today().isoformat()} | Selected scenario: {selected.scenario.name}",
            styles["subtitle"],
        ),
        _rule_table(GREEN),
        Paragraph("Executive Technical Summary", styles["section"]),
        Paragraph(_summary_text(document), styles["body"]),
        _metric_table(
            (
                ("Selected scenario", selected.scenario.name),
                ("Drum speed", f"{selected.scenario.speed_rpm:.1f} RPM"),
                ("Imbalance force", f"{selected.results.imbalance_force_n:.2f} N"),
                ("Shaft von Mises", f"{selected.results.von_mises_pa / 1.0e6:.2f} MPa"),
                ("Analytical yield FoS", f"{selected.results.factor_of_safety:.2f}"),
                ("Structural provenance", selected.provenance),
            ),
            styles,
        ),
        Paragraph("Physical Geometry And Drivetrain Configuration", styles["section"]),
        Paragraph(
            "The displayed geometry comes from the supplied CycleWash STL assembly. It is rendered here as a deterministic display illustration; it is not a solved FEA result or a CFD visualization.",
            styles["body"],
        ),
        _image_flowable(_assembly_figure(stl_root), CONTENT_WIDTH, 2.48 * inch),
        Paragraph("Simplified STL assembly illustration - schematic/analytical display only.", styles["caption"]),
        _symbol_table(document.project_dimensions, styles),
        Spacer(1, 4),
        Paragraph("Three-Scenario Comparison", styles["section"]),
        Paragraph(
            "Gentle, Normal, and Heavy are fixed operating points. Their engineering values below are the frozen shared-document results, with no recalculation in this exporter.",
            styles["body"],
        ),
        _scenario_table(document.scenario_reports, styles),
        _image_flowable(_comparison_chart(document.scenario_reports), CONTENT_WIDTH, 2.08 * inch),
        Paragraph("Scenario comparison chart - analytical values; not CFD or solved FEA.", styles["caption"]),
        Paragraph(f"Detailed Selected Scenario: {selected.scenario.name}", styles["section"]),
        Paragraph(
            f"This detailed section preserves {selected.provenance} provenance for the scenario load calculations. "
            "It presents water, imbalance, shaft bending, torsion, combined stress, and factor-of-safety values from the selected frozen report data.",
            styles["body"],
        ),
        _selected_result_table(selected, styles),
        _image_flowable(_imbalance_figure(selected), CONTENT_WIDTH, 1.85 * inch),
        Paragraph("Rotating unbalanced-load schematic - analytical force direction at the documented phase reference.", styles["caption"]),
    ]
    formula_blocks = [_formula_block(formula, styles) for formula in document.formulas]
    story.append(
        KeepTogether(
            [
                Paragraph("Formula Catalogue", styles["section"]),
                Paragraph(document.units_note, styles["body"]),
                formula_blocks.pop(0),
            ]
        )
    )
    for formula in formula_blocks:
        story.append(formula)
    story.extend(_closing_sections(document, styles))
    return story


def _summary_text(document: ReportDocument) -> str:
    selected = document.selected_report
    return (
        f"CycleWash is evaluated at three approved manual-drive operating scenarios. The selected {selected.scenario.name} case uses "
        f"{selected.scenario.human_power_w:.0f} W at {selected.scenario.speed_rpm:.0f} RPM with "
        f"{selected.scenario.laundry_mass_kg:.1f} kg effective wet laundry. {document.engineering_interpretation}"
    )


def _scenario_table(reports: Iterable[ScenarioReport], styles: dict[str, ParagraphStyle]) -> Table:
    rows = [["Scenario", "Speed\nRPM", "Power\nW", "Fill\n%", "Wet laundry\nkg", "Imbalance\nN", "von Mises\nMPa", "FoS\n-"]]
    for report in reports:
        scenario = report.scenario
        result = report.results
        rows.append(
            [
                scenario.name,
                f"{scenario.speed_rpm:.0f}",
                f"{scenario.human_power_w:.0f}",
                f"{scenario.fill_fraction * 100:.0f}",
                f"{scenario.laundry_mass_kg:.1f}",
                f"{result.imbalance_force_n:.1f}",
                f"{result.von_mises_pa / 1e6:.2f}",
                f"{result.factor_of_safety:.2f}",
            ]
        )
    return _styled_table(rows, [0.85 * inch, 0.46 * inch, 0.46 * inch, 0.42 * inch, 0.63 * inch, 0.58 * inch, 0.66 * inch, 0.42 * inch], styles)


def _selected_result_table(selected: ScenarioReport, styles: dict[str, ParagraphStyle]) -> Table:
    result = selected.results
    analytical = result.analytical
    entries = (
        ("Retained water mass", f"{analytical.retained_water_mass_kg:.3f} kg"),
        ("Design water pressure", f"{analytical.design_water_pressure_pa:.1f} Pa"),
        ("Design torque", f"{analytical.design_torque_n_m:.3f} N m"),
        ("Chain force", f"{analytical.chain_force_n:.2f} N"),
        ("Total shaft moment", f"{result.total_moment_n_m:.3f} N m"),
        ("Bending stress", f"{result.bending_stress_pa / 1e6:.3f} MPa"),
        ("Torsional shear", f"{analytical.shaft_torsional_shear_pa / 1e6:.3f} MPa"),
        ("Combined von Mises stress", f"{result.von_mises_pa / 1e6:.3f} MPa"),
        ("Yield factor of safety", f"{result.factor_of_safety:.3f}"),
    )
    return _metric_table(entries, styles)


def _formula_block(formula: FormulaDefinition, styles: dict[str, ParagraphStyle]) -> Flowable:
    symbol_table = _symbol_table(formula.symbols, styles)
    equation = _equation_markup(formula.html)
    if formula.identifier in {"shaft_bending_and_torsion", "fea_result_definitions"}:
        equation = equation.replace("; ", ";<br/>")
    content: list[Flowable] = [
        Paragraph(formula.title, styles["subsection"]),
        Paragraph(equation, styles["equation"]),
        Paragraph(f"<b>Evaluated substitution:</b> {formula.evaluated}", styles["body"]),
        symbol_table,
        Spacer(1, 3),
        Paragraph(formula.explanation, styles["body"]),
    ]
    return KeepTogether(content)


def _closing_sections(document: ReportDocument, styles: dict[str, ParagraphStyle]) -> list[Flowable]:
    closing: list[Flowable] = []
    selected = document.selected_report
    if selected.fea_summary:
        closing.extend(
            [
                Paragraph("Exact FEA Result And Provenance", styles["section"]),
                Paragraph(
                    "The following cached metrics are separately identified as exact Stage 1 solved results. They remain distinct from the analytical load estimate and do not represent transient structural analysis or CFD.",
                    styles["body"],
                ),
                _bullet_table(selected.fea_summary, styles, PALE_GREEN),
            ]
        )
    else:
        closing.extend(
            [
                Paragraph("FEA Result And Provenance", styles["section"]),
                Paragraph(
                    "No exact cached Stage 1 FEA package matched the selected scenario request. Structural values in this report retain Analytical load estimate provenance.",
                    styles["body"],
                ),
            ]
        )
    closing.extend(
        [
            Paragraph("Assumptions", styles["section"]),
            _bullet_table(document.assumptions, styles, PALE_BLUE),
            Paragraph("Limitations", styles["section"]),
            _bullet_table(document.limitations, styles, PALE_RED),
            Paragraph("Engineering Interpretation", styles["section"]),
            Paragraph(document.engineering_interpretation, styles["body"]),
            Paragraph("Conclusion", styles["section"]),
            Paragraph(document.conclusion, styles["body"]),
        ]
    )
    return closing


def _symbol_table(symbols: Iterable[object], styles: dict[str, ParagraphStyle]) -> Table:
    rows: list[list[object]] = [["Symbol", "Meaning", "SI Unit", "Source"]]
    for symbol in symbols:
        rows.append(
            [
                Paragraph(getattr(symbol, "symbol"), styles["small"]),
                Paragraph(getattr(symbol, "meaning"), styles["small"]),
                Paragraph(getattr(symbol, "unit"), styles["small"]),
                Paragraph(getattr(symbol, "source"), styles["small"]),
            ]
        )
    return _styled_table(rows, [0.93 * inch, 2.66 * inch, 0.76 * inch, 2.12 * inch], styles)


def _metric_table(entries: Iterable[tuple[str, str]], styles: dict[str, ParagraphStyle]) -> Table:
    rows = [[Paragraph(f"<b>{label}</b>", styles["small"]), Paragraph(value, styles["small"])] for label, value in entries]
    table = Table(rows, colWidths=[2.45 * inch, 4.02 * inch], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), PALE_BLUE),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#C4D2DA")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _styled_table(rows: list[list[object]], widths: list[float], styles: dict[str, ParagraphStyle]) -> Table:
    formatted_rows: list[list[object]] = []
    for row_index, row in enumerate(rows):
        formatted_rows.append(
            [
                Paragraph(str(cell).replace("\n", "<br/>"), styles["table_header"]) if row_index == 0 else cell
                for cell in row
            ]
        )
    table = Table(formatted_rows, colWidths=widths, repeatRows=1, hAlign="LEFT", splitByRow=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), DARK_BLUE),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F6F9FA")]),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#BFCED6")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _bullet_table(items: Iterable[str], styles: dict[str, ParagraphStyle], background: colors.Color) -> Table:
    rows = [[Paragraph("-", styles["small"]), Paragraph(item, styles["small"])] for item in items]
    table = Table(rows, colWidths=[0.18 * inch, 6.29 * inch], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), background),
                ("BOX", (0, 0), (-1, -1), 0.35, colors.HexColor("#C4D2DA")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D4DEE4")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _rule_table(color: colors.Color) -> Table:
    table = Table([[""]], colWidths=[CONTENT_WIDTH], rowHeights=[2])
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), color)]))
    return table


def _equation_markup(html: str) -> str:
    replacements = {
        "<div>": "",
        "</div>": "",
        "&omega;": "ω",
        "&pi;": "π",
        "&rho;": "ρ",
        "&theta;": "θ",
        "&sigma;": "σ",
        "&tau;": "τ",
        "&radic;": "√",
    }
    for source, target in replacements.items():
        html = html.replace(source, target)
    return html


def _image_flowable(png: bytes, width: float, height: float) -> Image:
    return Image(BytesIO(png), width=width, height=height, hAlign="CENTER")


def _assembly_figure(stl_root: Path) -> bytes:
    parts = []
    for name, filename, color, kind in _ASSEMBLY_FILES:
        part = load_stl_part(
            StlPartSpec(name=name, source=stl_root / filename, material_color=color, component_kind=kind)
        )
        parts.append(normalize_stl_part(part).part)
    image = PillowImage.new("RGB", (1100, 430), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 1099, 429), fill="#F7FAFB")
    all_vertices = np.concatenate([part.vertices for part in parts], axis=0)
    projected = _isometric_projection(all_vertices)
    minimum = projected.min(axis=0)
    span = np.maximum(projected.max(axis=0) - minimum, 1e-9)

    def project(vertices: np.ndarray) -> np.ndarray:
        points = _isometric_projection(vertices)
        normalized = (points - minimum) / span
        return np.column_stack((110 + normalized[:, 0] * 710, 370 - normalized[:, 1] * 315))

    for part in parts:
        coordinates = project(part.vertices)
        sample_count = min(part.faces.shape[0], 650)
        face_indices = np.linspace(0, part.faces.shape[0] - 1, sample_count, dtype=int)
        fill = part.material_color
        for face in part.faces[face_indices]:
            points = [tuple(point) for point in coordinates[face]]
            draw.polygon(points, fill=fill, outline="#FFFFFF")
    font = ImageFont.load_default()
    draw.rectangle((855, 52, 1060, 338), fill="#FFFFFF", outline="#BFCED6", width=2)
    draw.text((877, 72), "STL display key", fill="#123047", font=font)
    for index, part in enumerate(parts):
        y = 112 + index * 48
        draw.rectangle((877, y, 897, y + 20), fill=part.material_color, outline="#48606D")
        draw.text((910, y + 5), part.name, fill="#263642", font=font)
    draw.text((110, 392), "Simplified physical assembly view", fill="#48606D", font=font)
    return _png_bytes(image)


def _comparison_chart(reports: Iterable[ScenarioReport]) -> bytes:
    report_list = list(reports)
    image = PillowImage.new("RGB", (1100, 360), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    draw.rectangle((0, 0, 1099, 359), fill="#F7FAFB")
    chart_left, chart_bottom, chart_top = 100, 295, 58
    group_width = 250
    colors_by_metric = ("#1A6B57", "#123047", "#B6403B")
    metric_labels = ("Drum speed (RPM)", "Imbalance force (N)", "von Mises (MPa)")
    metric_values = (
        [report.scenario.speed_rpm for report in report_list],
        [report.results.imbalance_force_n for report in report_list],
        [report.results.von_mises_pa / 1e6 for report in report_list],
    )
    draw.line((chart_left, chart_bottom, 1015, chart_bottom), fill="#65808E", width=2)
    for index, report in enumerate(report_list):
        base_x = 155 + index * group_width
        draw.text((base_x + 18, 310), report.scenario.name, fill="#123047", font=font)
        for metric_index, values in enumerate(metric_values):
            height = (values[index] / max(values)) * (chart_bottom - chart_top)
            x = base_x + metric_index * 42
            draw.rectangle((x, chart_bottom - height, x + 29, chart_bottom), fill=colors_by_metric[metric_index])
            draw.text((x, max(36, chart_bottom - height - 13)), f"{values[index]:.1f}", fill="#263642", font=font)
    for index, label in enumerate(metric_labels):
        x = 817
        y = 68 + index * 46
        draw.rectangle((x, y, x + 18, y + 18), fill=colors_by_metric[index])
        draw.text((x + 29, y + 4), label, fill="#263642", font=font)
    return _png_bytes(image)


def _imbalance_figure(selected: ScenarioReport) -> bytes:
    result = selected.results
    image = PillowImage.new("RGB", (1100, 300), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    draw.rectangle((0, 0, 1099, 299), fill="#F7FAFB")
    center = (280, 150)
    draw.ellipse((105, 25, 455, 275), outline="#123047", width=5)
    draw.ellipse((232, 102, 328, 198), outline="#1A6B57", width=4)
    draw.line((center[0], center[1], 420, 84), fill="#B6403B", width=7)
    draw.polygon([(420, 84), (391, 82), (404, 109)], fill="#B6403B")
    draw.ellipse((402, 66, 438, 102), fill="#D99B33", outline="#8C6420")
    draw.text((94, 279), "Drum section", fill="#48606D", font=font)
    draw.text((475, 72), f"F_u = {result.imbalance_force_n:.2f} N", fill="#B6403B", font=font)
    draw.text((475, 110), f"m_u = {selected.scenario.laundry_mass_kg:.2f} kg", fill="#263642", font=font)
    draw.text((475, 136), f"e = {selected.scenario.eccentricity_m:.3f} m", fill="#263642", font=font)
    draw.text((475, 162), f"speed = {selected.scenario.speed_rpm:.1f} RPM", fill="#263642", font=font)
    draw.text((475, 208), "Force rotates with the drum; drawing is a phase-reference schematic.", fill="#48606D", font=font)
    return _png_bytes(image)


def _isometric_projection(vertices: np.ndarray) -> np.ndarray:
    return np.column_stack(
        (
            vertices[:, 0] - 0.62 * vertices[:, 1],
            vertices[:, 2] + 0.34 * vertices[:, 0] + 0.24 * vertices[:, 1],
        )
    )


def _png_bytes(image: PillowImage.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG", optimize=False)
    return output.getvalue()


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#BFCED6"))
    canvas.line(MARGIN, 11 * mm, PAGE_WIDTH - MARGIN, 11 * mm)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#48606D"))
    canvas.drawString(MARGIN, 7.5 * mm, "CycleWash Technical Evaluation | Analytical report supplement")
    canvas.drawRightString(PAGE_WIDTH - MARGIN, 7.5 * mm, f"Page {doc.page}")
    canvas.restoreState()


__all__ = ["build_report_pdf"]
