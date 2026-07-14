"""Behavior tests for the printable CycleWash technical-report PDF."""

from __future__ import annotations

import base64
from pathlib import Path
import re
import unittest
import zlib

from cyclewash_technical_report import LIMITATIONS_NOTE, build_report_document, core_formulas
from cyclewash_technical_report_pdf import build_report_pdf


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
        self.assertEqual(1, report_text.count(LIMITATIONS_NOTE))
        for removed in (
            "Formula Catalogue",
            "Exact FEA Result And Provenance",
            "Assumptions",
            "Physical Geometry And Drivetrain Configuration",
        ):
            self.assertNotIn(removed, report_text)

    def test_exported_pdf_bytes_and_embedded_images_are_deterministic(self) -> None:
        first_pdf = build_report_pdf(self.document, PROJECT_ROOT)
        second_pdf = build_report_pdf(self.document, PROJECT_ROOT)

        self.assertEqual(first_pdf, second_pdf)
        self.assertEqual(1, len(re.findall(rb"/Subtype\s*/Image\b", first_pdf)))

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
