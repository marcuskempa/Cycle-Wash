"""Behavior tests for the printable CycleWash technical-report PDF."""

from __future__ import annotations

import base64
from pathlib import Path
import re
import unittest
import zlib

from cyclewash_technical_report import build_report_document
from cyclewash_technical_report_pdf import build_report_pdf


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
        text_fragments.extend(
            fragment.decode("utf-16-be", errors="ignore")
            for fragment in re.findall(rb"(?:[0-9A-Fa-f]{4}){2,}", decoded)
        )
        text_fragments.extend(
            fragment.decode("latin-1", errors="ignore")
            for fragment in re.findall(rb"\(([^()]*)\)", decoded)
        )
    return " ".join(text_fragments)


class CycleWashTechnicalReportPdfTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = build_report_document("Normal", PROJECT_ROOT / "fea_results")

    def test_builds_multi_page_pdf_with_required_engineering_content(self) -> None:
        pdf_bytes = build_report_pdf(self.document, PROJECT_ROOT)
        report_text = _extract_pdf_text(pdf_bytes)

        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertGreaterEqual(len(re.findall(rb"/Type\s*/Page\b", pdf_bytes)), 4)
        for expected_text in (
            "CycleWash Technical Evaluation",
            "Executive Technical Summary",
            "Physical Geometry And Drivetrain Configuration",
            "Gentle",
            "Normal",
            "Heavy",
            "Detailed Selected Scenario: Normal",
            "Formula Catalogue",
            "Drivetrain Speed Ratio",
            "Unbalanced Wet-Laundry Load",
            "Shaft Bending And Torsion",
            "Combined Stress And Factor Of Safety",
            "Symbol",
            "SI Unit",
            "Solved Stage 1 FEA",
            "Assumptions",
            "Limitations",
            "Engineering Interpretation",
            "Conclusion",
            "schematic/analytical",
        ):
            self.assertIn(expected_text, report_text)

    def test_exported_content_is_deterministic_for_a_frozen_document(self) -> None:
        first_text = _extract_pdf_text(build_report_pdf(self.document, PROJECT_ROOT))
        second_text = _extract_pdf_text(build_report_pdf(self.document, PROJECT_ROOT))

        self.assertEqual(first_text, second_text)

    def test_rejects_invalid_document_and_stl_root(self) -> None:
        with self.assertRaises(TypeError):
            build_report_pdf(None, PROJECT_ROOT)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            build_report_pdf(self.document, PROJECT_ROOT / "missing-stl-root")


if __name__ == "__main__":
    unittest.main()
