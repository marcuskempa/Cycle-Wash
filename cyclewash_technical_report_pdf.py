"""Printable ReportLab export for the frozen CycleWash technical-report document."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image as PillowImage
from PIL import ImageDraw, ImageFont
from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch, mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from cyclewash_geometry_policy import normalize_stl_part
from cyclewash_structural_visualizer import StlPartSpec, load_stl_part
from cyclewash_technical_report import LIMITATIONS_NOTE, FormulaDefinition, ReportDocument, ScenarioReport, core_formulas


PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 16 * mm
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
DARK_BLUE = colors.HexColor("#123047")
GREEN = colors.HexColor("#1A6B57")
PALE_BLUE = colors.HexColor("#EAF1F5")
PDF_REPORT_SCHEMA_VERSION = "cyclewash-pdf-v1"

_ASSEMBLY_FILES = (
    ("Enclosure", "enclosure.stl", "#6F8795", "casing"),
    ("Drum", "Inner_Drum.stl", "#1A6B57", "drum"),
    ("Shaft", "shaft.stl", "#B6403B", "shaft"),
    ("Gear", "gear.stl", "#D99B33", "gear"),
)


def pdf_report_fingerprint() -> str:
    """Return a cache key for the PDF schema and renderer implementation."""

    source_bytes = Path(__file__).read_bytes()
    payload = PDF_REPORT_SCHEMA_VERSION.encode("utf-8") + b"\0" + source_bytes
    return sha256(payload).hexdigest()


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
    selected_metric_entries = (
        ("Selected scenario", selected.scenario.name),
        ("Drum speed", f"{selected.scenario.speed_rpm:.1f} RPM"),
        ("Imbalance force", f"{selected.results.imbalance_force_n:.2f} N"),
        ("Shaft von Mises", f"{selected.results.von_mises_pa / 1.0e6:.2f} MPa"),
        ("Analytical yield FoS", f"{selected.results.factor_of_safety:.2f}"),
        (
            "Water mass",
            f"{selected.results.analytical.retained_water_mass_kg:.1f} kg",
        ),
    )
    return [
        Paragraph("CycleWash Technical Evaluation", styles["title"]),
        Paragraph(_summary_text(document), styles["body"]),
        _image_flowable(_assembly_figure(stl_root), CONTENT_WIDTH, 2.35 * inch),
        _metric_table(selected_metric_entries, styles),
        Paragraph("Operating Scenario Comparison", styles["section"]),
        _scenario_table(document.scenario_reports, styles),
        PageBreak(),
        Paragraph("Core Engineering Calculations", styles["section"]),
        *[_compact_formula_block(formula, styles) for formula in core_formulas(document)],
        Paragraph("Engineering Interpretation", styles["section"]),
        Paragraph(document.engineering_interpretation, styles["body"]),
        Paragraph("Conclusion", styles["section"]),
        Paragraph(_conclusion_text(document), styles["body"]),
        Paragraph("Limitations", styles["section"]),
        Paragraph(LIMITATIONS_NOTE, styles["body"]),
    ]


def _summary_text(document: ReportDocument) -> str:
    selected = document.selected_report
    return (
        "CycleWash compares three approved manual-drive washing scenarios using "
        "simplified drivetrain, retained-water, imbalance, and shaft calculations "
        "for an introductory engineering design study. Page 1 summarizes the "
        f"selected {selected.scenario.name} operating point and compares it with "
        "the other approved cases."
    )


def _conclusion_text(document: ReportDocument) -> str:
    selected = document.selected_report
    return (
        f"For the {selected.scenario.name} operating point, the estimated shaft "
        f"factor of safety is {selected.results.factor_of_safety:.2f}. These results "
        "support transparent concept comparison; detailed dynamic, fatigue, bearing, "
        "and joint validation would still be required before fabrication."
    )


def _scenario_table(
    reports: Iterable[ScenarioReport], styles: dict[str, ParagraphStyle]
) -> Table:
    rows = [
        [
            "Scenario",
            "Speed\nRPM",
            "Power\nW",
            "Fill\n%",
            "Wet laundry\nkg",
            "Imbalance\nN",
            "von Mises\nMPa",
            "FoS\n-",
        ]
    ]
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
    return _styled_table(
        rows,
        [
            0.85 * inch,
            0.55 * inch,
            0.55 * inch,
            0.50 * inch,
            0.72 * inch,
            0.67 * inch,
            0.80 * inch,
            0.64 * inch,
        ],
        styles,
    )


def _compact_formula_block(formula: FormulaDefinition, styles: dict[str, ParagraphStyle]) -> Flowable:
    return KeepTogether(
        [
            Paragraph(formula.title, styles["subsection"]),
            Paragraph(_equation_markup(formula.html), styles["equation"]),
            Paragraph(_equation_markup(formula.evaluated_html), styles["equation"]),
            Spacer(1, 3),
        ]
    )


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
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rectangle((0, 0, 1099, 429), fill="#F7FAFB")
    all_vertices = np.concatenate([part.vertices for part in parts], axis=0)
    projected, _ = _orthographic_z_up_projection(all_vertices)
    minimum = projected.min(axis=0)
    span = projected.max(axis=0) - minimum
    if not np.isfinite(projected).all() or np.any(span <= 0):
        raise ValueError("assembly STL vertices must have a finite projected span")
    scale = min(710 / span[0], 315 / span[1])
    offset = np.array(((710 - span[0] * scale) / 2, (315 - span[1] * scale) / 2))

    def project(vertices: np.ndarray) -> np.ndarray:
        points, _ = _orthographic_z_up_projection(vertices)
        projected_points = (points - minimum) * scale + offset
        return np.column_stack((110 + projected_points[:, 0], 370 - projected_points[:, 1]))

    triangles: list[tuple[float, int, int, np.ndarray, tuple[int, int, int, int]]] = []
    for part_index, part in enumerate(parts):
        coordinates = project(part.vertices)
        _, vertex_depth = _orthographic_z_up_projection(part.vertices)
        sample_count = min(part.faces.shape[0], 16000)
        face_indices = np.linspace(0, part.faces.shape[0] - 1, sample_count, dtype=int)
        fill = _assembly_fill(part.component_kind, part.material_color)
        for face_index in face_indices:
            face = part.faces[face_index]
            triangles.append(
                (float(vertex_depth[face].mean()), part_index, int(face_index), coordinates[face], fill)
            )
    for _, _, _, triangle, fill in sorted(triangles, key=lambda item: item[:3]):
        draw.polygon([tuple(point) for point in triangle], fill=fill)
    font = ImageFont.load_default()
    draw.rectangle((855, 52, 1060, 338), fill="#FFFFFF", outline="#BFCED6", width=2)
    draw.text((877, 72), "STL display key", fill="#123047", font=font)
    for index, part in enumerate(parts):
        y = 112 + index * 48
        draw.rectangle((877, y, 897, y + 20), fill=part.material_color, outline="#48606D")
        draw.text((910, y + 5), part.name, fill="#263642", font=font)
    draw.text((110, 392), "Simplified physical assembly view", fill="#48606D", font=font)
    return _png_bytes(image)


def _orthographic_z_up_projection(vertices: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Project the Blender Z-up assembly from the viewer's +X/-Y camera direction."""

    vertices = np.asarray(vertices, dtype=float)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all():
        raise ValueError("assembly STL vertices must be finite XYZ coordinates")
    camera_direction = np.array((1.15, -0.92, 0.82))
    camera_direction /= np.linalg.norm(camera_direction)
    screen_x = np.cross(np.array((0.0, 0.0, 1.0)), camera_direction)
    screen_x /= np.linalg.norm(screen_x)
    screen_y = np.cross(camera_direction, screen_x)
    return (
        np.column_stack((vertices @ screen_x, vertices @ screen_y)),
        vertices @ camera_direction,
    )


def _assembly_fill(component_kind: str, material_color: str) -> tuple[int, int, int, int]:
    rgb = tuple(int(material_color[index : index + 2], 16) for index in (1, 3, 5))
    return (*rgb, 150 if component_kind == "casing" else 255)


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


__all__ = ["PDF_REPORT_SCHEMA_VERSION", "build_report_pdf", "pdf_report_fingerprint"]
