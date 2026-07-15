"""Behavior tests for the printable CycleWash technical-report PDF."""

from __future__ import annotations

import base64
import hashlib
from io import BytesIO
from pathlib import Path
import re
import unittest
from unittest.mock import patch
import zlib

import numpy as np
from PIL import Image, ImageDraw

from cyclewash_technical_report import LIMITATIONS_NOTE, build_report_document, core_formulas
from cyclewash_technical_report_pdf import (
    PDF_REPORT_SCHEMA_VERSION,
    _assembly_figure,
    _paint_triangles_far_to_near,
    _report_styles,
    build_report_pdf,
    pdf_report_fingerprint,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _pdf_literal_strings(content: bytes) -> list[str]:
    """Decode literal strings, including ReportLab escapes and nested parentheses."""

    strings: list[str] = []
    index = 0
    escape_map = {
        ord("n"): b"\n",
        ord("r"): b"\r",
        ord("t"): b"\t",
        ord("b"): b"\b",
        ord("f"): b"\f",
    }
    while index < len(content):
        if content[index] != ord("("):
            index += 1
            continue
        index += 1
        depth = 1
        decoded = bytearray()
        while index < len(content) and depth:
            byte = content[index]
            if byte == ord("\\"):
                index += 1
                if index >= len(content):
                    break
                escaped = content[index]
                if ord("0") <= escaped <= ord("7"):
                    octal = bytearray([escaped])
                    while (
                        len(octal) < 3
                        and index + 1 < len(content)
                        and ord("0") <= content[index + 1] <= ord("7")
                    ):
                        index += 1
                        octal.append(content[index])
                    decoded.append(int(octal.decode("ascii"), 8))
                elif escaped == ord("\r"):
                    if index + 1 < len(content) and content[index + 1] == ord("\n"):
                        index += 1
                elif escaped != ord("\n"):
                    decoded.extend(escape_map.get(escaped, bytes([escaped])))
            elif byte == ord("("):
                depth += 1
                decoded.append(byte)
            elif byte == ord(")"):
                depth -= 1
                if depth:
                    decoded.append(byte)
            else:
                decoded.append(byte)
            index += 1
        strings.append(decoded.decode("latin-1", errors="ignore"))
    return strings


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract ASCII/Unicode text from the ReportLab Flate content streams."""

    text_fragments: list[str] = []
    for stream in re.findall(rb"stream\r?\n(.*?)endstream", pdf_bytes, re.DOTALL):
        stream = stream.rstrip(b"\r\n")
        if stream.endswith(b"~>"):
            stream = base64.a85decode(stream[:-2])
        try:
            decoded = zlib.decompress(stream)
        except zlib.error:
            continue
        if b"BT" not in decoded or b"ET" not in decoded:
            continue
        text_fragments.extend(
            fragment.decode("utf-16-be", errors="ignore")
            for fragment in re.findall(rb"(?:[0-9A-Fa-f]{4}){2,}", decoded)
        )
        text_fragments.extend(_pdf_literal_strings(decoded))
    return " ".join(text_fragments)


class CycleWashTechnicalReportPdfTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = build_report_document("Normal", PROJECT_ROOT / "fea_results")

    def test_builds_exact_two_page_introductory_report(self) -> None:
        pdf_bytes = build_report_pdf(self.document, PROJECT_ROOT)
        report_text = " ".join(_extract_pdf_text(pdf_bytes).split())

        self.assertEqual(2, len(re.findall(rb"/Type\s*/Page\b", pdf_bytes)))
        self.assertEqual(1, len(re.findall(rb"/Subtype\s*/Image\b", pdf_bytes)))
        for formula in core_formulas(self.document):
            self.assertIn(formula.title, report_text)
        source = Path(build_report_pdf.__code__.co_filename).read_text(encoding="utf-8")
        self.assertIn(
            'Paragraph(_equation_markup(formula.evaluated_html), styles["equation"]),',
            source,
        )
        self.assertEqual(1, report_text.count(LIMITATIONS_NOTE))
        for removed in (
            "Formula Catalogue",
            "Exact FEA Result And Provenance",
            "Assumptions",
            "Physical Geometry And Drivetrain Configuration",
            "Variables:",
            "Evaluated:",
        ):
            self.assertNotIn(removed, report_text)

    def test_exported_pdf_bytes_and_embedded_images_are_deterministic(self) -> None:
        first_pdf = build_report_pdf(self.document, PROJECT_ROOT)
        second_pdf = build_report_pdf(self.document, PROJECT_ROOT)

        self.assertEqual(first_pdf, second_pdf)
        self.assertEqual(1, len(re.findall(rb"/Subtype\s*/Image\b", first_pdf)))

    def test_pdf_fingerprint_is_schema_versioned_sha256(self) -> None:
        fingerprint = pdf_report_fingerprint()
        renderer_source = Path(pdf_report_fingerprint.__code__.co_filename).read_bytes()
        expected = hashlib.sha256(
            PDF_REPORT_SCHEMA_VERSION.encode("utf-8") + b"\0" + renderer_source
        ).hexdigest()

        self.assertEqual(64, len(fingerprint))
        self.assertRegex(fingerprint, r"^[0-9a-f]{64}$")
        self.assertTrue(PDF_REPORT_SCHEMA_VERSION)
        self.assertEqual(expected, fingerprint)
        with patch(
            "cyclewash_technical_report_pdf.PDF_REPORT_SCHEMA_VERSION",
            "cyclewash-pdf-v2",
        ):
            self.assertNotEqual(fingerprint, pdf_report_fingerprint())

    def test_assembly_raster_is_deterministic_colored_and_outline_free(self) -> None:
        first_png = _assembly_figure(PROJECT_ROOT)
        second_png = _assembly_figure(PROJECT_ROOT)
        pixels = np.asarray(Image.open(BytesIO(first_png)).convert("RGB"))
        schematic = pixels[40:380, 90:830]
        background = np.array([247, 250, 251])
        non_background = np.any(schematic != background, axis=2)
        white = np.all(schematic == 255, axis=2)
        red, green, blue = (schematic[:, :, index] for index in range(3))

        self.assertEqual(first_png, second_png)
        self.assertGreater(non_background.sum(), schematic.shape[0] * schematic.shape[1] * 0.12)
        self.assertGreater(
            ((red >= 135) & (green >= 150) & (blue >= 155)).sum(),
            500,
        )
        self.assertGreater(((green >= 85) & (green > red * 1.2) & (green > blue * 1.1)).sum(), 250)
        self.assertGreater(((red >= 130) & (red > green * 1.3) & (red > blue * 1.3)).sum(), 100)
        self.assertGreater(((red >= 170) & (green >= 100) & (blue <= 90)).sum(), 100)
        self.assertLess(white.sum(), schematic.shape[0] * schematic.shape[1] * 0.01)

    def test_triangle_painter_draws_nearer_face_over_the_far_face(self) -> None:
        image = Image.new("RGB", (20, 20), "white")
        draw = ImageDraw.Draw(image)
        far_triangle = np.array(((2, 2), (17, 2), (2, 17)))
        near_triangle = np.array(((2, 2), (17, 2), (17, 17)))

        _paint_triangles_far_to_near(
            draw,
            (
                (1.0, 0, 0, far_triangle, (190, 64, 59, 255)),
                (2.0, 0, 1, near_triangle, (26, 107, 87, 255)),
            ),
        )

        self.assertEqual((26, 107, 87), image.getpixel((10, 5)))

    def test_equation_style_has_clearance_for_subscripts_and_dividers(self) -> None:
        equation_style = _report_styles()["equation"]

        self.assertGreaterEqual(equation_style.leading, 17)
        self.assertGreaterEqual(equation_style.borderPadding, 8)
        self.assertGreaterEqual(equation_style.leading - equation_style.fontSize, 6.5)
        self.assertGreaterEqual(_report_styles()["subsection"].spaceAfter, 8)

    def test_page_one_is_concise_and_pdf_states_provenance_once(self) -> None:
        from cyclewash_technical_report_pdf import (
            _report_styles,
            _scenario_table,
            _summary_text,
        )

        pdf_bytes = build_report_pdf(self.document, PROJECT_ROOT)
        report_text = " ".join(_extract_pdf_text(pdf_bytes).split())
        summary = _summary_text(self.document)
        table = _scenario_table(self.document.scenario_reports, _report_styles())

        self.assertNotIn(self.document.engineering_interpretation, summary)
        self.assertNotIn(self.document.selected_report.provenance, summary)
        self.assertEqual(
            1, report_text.count(self.document.selected_report.provenance)
        )
        self.assertEqual(
            1, report_text.count(self.document.engineering_interpretation)
        )
        water_mass = (
            self.document.selected_report.results.analytical.retained_water_mass_kg
        )
        self.assertIn(
            f"{water_mass:.1f} kg",
            report_text,
        )
        self.assertTrue(all(len(row) == 8 for row in table._cellvalues))

    def test_rejects_invalid_document_and_stl_root(self) -> None:
        with self.assertRaises(TypeError):
            build_report_pdf(None, PROJECT_ROOT)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            build_report_pdf(self.document, PROJECT_ROOT / "missing-stl-root")


if __name__ == "__main__":
    unittest.main()
