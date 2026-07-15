"""Single-file, offline CycleWash technical-report HTML export."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hashlib
import html
import json
import math
from pathlib import Path
from typing import Any, Final

import numpy as np

from cyclewash_geometry_policy import apply_closed_door_pose, normalize_stl_part
from cyclewash_html_animation import encode_typed_array
from cyclewash_structural_visualizer import AssemblyPart, StlPartSpec, load_stl_part
from cyclewash_technical_report import (
    LIMITATIONS_NOTE,
    FormulaDefinition,
    ReportDocument,
    core_formulas,
)


MODULE_DIRECTORY: Final[Path] = Path(__file__).resolve().parent
TEMPLATE_PATH: Final[Path] = MODULE_DIRECTORY / "cyclewash_technical_report_template.html"
THREE_BUNDLE_PATH: Final[Path] = MODULE_DIRECTORY / "assets" / "cyclewash-three-bundle.min.js"
MAX_TOTAL_TRIANGLES: Final[int] = 150_000
MAX_GEOMETRY_BYTES: Final[int] = 4 * 1024 * 1024
MAX_OFFLINE_HTML_BYTES: Final[int] = 8 * 1024 * 1024
VIEWER_PAYLOAD_SCHEMA_VERSION: Final[str] = "cyclewash-offline-report-v3"

def viewer_asset_fingerprint() -> str:
    """Hash every non-Python asset that changes generated viewer HTML."""

    digest = hashlib.sha256()
    digest.update(VIEWER_PAYLOAD_SCHEMA_VERSION.encode("utf-8"))
    for path in (TEMPLATE_PATH, THREE_BUNDLE_PATH):
        content = path.read_bytes()
        digest.update(path.name.encode("utf-8"))
        digest.update(len(content).to_bytes(8, byteorder="big"))
        digest.update(content)
    return digest.hexdigest()

_REQUIRED_PARTS: Final[tuple[tuple[str, str, str, str], ...]] = (
    ("enclosure", "enclosure.stl", "#9ca3af", "casing"),
    ("door", "door.stl", "#64748b", "door"),
    ("Inner Drum", "Inner_Drum.stl", "#60a5fa", "rotational"),
    ("gear", "gear.stl", "#f59e0b", "rotational"),
    ("shaft", "shaft.stl", "#d97706", "rotational"),
)
_OPTIONAL_PARTS: Final[tuple[tuple[str, str, str, str], ...]] = (
    ("Agitator", "Agitator.stl", "#38bdf8", "rotational"),
)
_ROTATING_TOKENS: Final[tuple[str, ...]] = ("inner drum", "drum", "gear", "shaft", "agitator")


def build_offline_report_html(document: ReportDocument, stl_root: str | Path) -> bytes:
    """Return a deterministic UTF-8 technical report that can open from disk."""

    if not isinstance(document, ReportDocument):
        raise TypeError("document must be a ReportDocument")
    parts = _load_normalized_parts(Path(stl_root))
    payload = _build_payload(document, parts)
    report_html = _render_report(document)
    rendered = _render_template(
        report_html=report_html,
        payload_json=_safe_json(payload),
        three_bundle=_offline_three_bundle(),
        selected_name=document.selected_report.scenario.name,
    )
    encoded = rendered.encode("utf-8")
    if len(encoded) > MAX_OFFLINE_HTML_BYTES:
        raise ValueError(
            f"offline HTML size {len(encoded):,} bytes exceeds the "
            f"{MAX_OFFLINE_HTML_BYTES:,}-byte output budget"
        )
    return encoded


def build_scenario_viewer_html(
    document: ReportDocument, selected_name: str, stl_root: str | Path
) -> str:
    """Return the standalone viewer portion, initialized to one fixed scenario."""

    if not isinstance(document, ReportDocument):
        raise TypeError("document must be a ReportDocument")
    names = {report.scenario.name for report in document.scenario_reports}
    if selected_name not in names:
        raise ValueError(f"unknown fixed scenario: {selected_name}")
    parts = _load_normalized_parts(Path(stl_root))
    payload = _build_payload(document, parts)
    return _render_template(
        report_html="",
        payload_json=_safe_json(payload),
        three_bundle=_offline_three_bundle(),
        selected_name=selected_name,
        viewer_only=True,
    )


def _load_normalized_parts(stl_root: Path) -> tuple[AssemblyPart, ...]:
    if not stl_root.is_dir():
        raise ValueError(f"STL root does not exist or is not a directory: {stl_root}")
    parts: list[AssemblyPart] = []
    for name, filename, color, component_kind in _REQUIRED_PARTS + _OPTIONAL_PARTS:
        path = stl_root / filename
        if not path.is_file():
            if (name, filename, color, component_kind) in _REQUIRED_PARTS:
                raise ValueError(f"missing required STL for {name}: {path}")
            continue
        try:
            source_part = load_stl_part(
                StlPartSpec(
                    name=name,
                    source=path,
                    material_color=color,
                    component_kind=component_kind,
                )
            )
            normalized = apply_closed_door_pose(normalize_stl_part(source_part).part)
        except (OSError, ValueError) as error:
            raise ValueError(f"unable to load STL for {name}: {path} ({error})") from error
        parts.append(normalized)
    return tuple(parts)


def _build_payload(document: ReportDocument, parts: tuple[AssemblyPart, ...]) -> dict[str, Any]:
    geometry_summary = _validate_geometry_budget(parts)
    geometry_parts = [_part_payload(part) for part in parts]
    shaft = next(part for part in parts if part.name.lower() == "shaft")
    shaft_vertices = np.asarray(shaft.vertices, dtype=float)
    rotation_origin = ((shaft_vertices.min(axis=0) + shaft_vertices.max(axis=0)) / 2.0).tolist()
    inner_drum = next(
        part for part in parts if _normalized_name(part.name) == "inner drum"
    )
    drum_vertices = np.asarray(inner_drum.vertices, dtype=float)
    drum_minimum = drum_vertices.min(axis=0)
    drum_maximum = drum_vertices.max(axis=0)
    drum_span = drum_maximum - drum_minimum
    if not np.all(np.isfinite(drum_span)) or np.any(drum_span <= 0.0):
        raise ValueError("normalized Inner Drum geometry must have finite non-zero bounds")
    drum_center = (drum_minimum + drum_maximum) / 2.0
    scenarios = {
        report.scenario.name: _scenario_payload(report)
        for report in document.scenario_reports
    }
    return {
        "schema_version": VIEWER_PAYLOAD_SCHEMA_VERSION,
        "selected_scenario": document.selected_report.scenario.name,
        "report": {
            "conclusion": document.conclusion,
            "engineering_interpretation": document.engineering_interpretation,
            "units_note": document.units_note,
        },
        "geometry": {
            "parts": geometry_parts,
            "rotation_axis": [1.0, 0.0, 0.0],
            "rotation_origin": [float(value) for value in rotation_origin],
            "drum_envelope": {
                "center_m": [float(value) for value in drum_center],
                "span_m": [float(value) for value in drum_span],
            },
            "source": "Normalized authoritative local STL geometry, embedded once.",
            "summary": geometry_summary,
        },
        "scenarios": scenarios,
    }


def _validate_geometry_budget(parts: tuple[AssemblyPart, ...]) -> dict[str, int]:
    triangle_count = sum(part.triangle_count for part in parts)
    geometry_bytes = sum(
        int(np.asarray(part.vertices).size + np.asarray(part.faces).size) * 4
        for part in parts
    )
    if triangle_count > MAX_TOTAL_TRIANGLES:
        raise ValueError(
            f"STL assembly has {triangle_count:,} triangles and exceeds the "
            f"{MAX_TOTAL_TRIANGLES:,}-triangle budget"
        )
    if geometry_bytes > MAX_GEOMETRY_BYTES:
        raise ValueError(
            f"STL assembly needs {geometry_bytes:,} typed-array bytes and exceeds the "
            f"{MAX_GEOMETRY_BYTES:,}-byte geometry byte budget"
        )
    return {
        "triangle_count": triangle_count,
        "typed_array_bytes": geometry_bytes,
        "triangle_budget": MAX_TOTAL_TRIANGLES,
        "typed_array_byte_budget": MAX_GEOMETRY_BYTES,
    }


def _part_payload(part: AssemblyPart) -> dict[str, Any]:
    vertices = np.asarray(part.vertices, dtype=np.float64)
    faces = np.asarray(part.faces, dtype=np.uint32)
    normalized_name = _normalized_name(part.name)
    return {
        "name": part.name,
        "kind": part.component_kind,
        "color": part.material_color,
        "opacity": 0.5 if normalized_name in {"enclosure", "door"} else 1.0,
        "rotating": any(token in normalized_name for token in _ROTATING_TOKENS),
        "geometry": {
            "positions": {
                "dtype": "float32",
                "shape": [int(value) for value in vertices.shape],
                "base64": encode_typed_array(vertices, "float32"),
            },
            "indices": {
                "dtype": "uint32",
                "shape": [int(value) for value in faces.shape],
                "base64": encode_typed_array(faces, "uint32"),
            },
        },
    }


def _scenario_payload(report: Any) -> dict[str, Any]:
    scenario = report.scenario
    results = report.results
    analytical = results.analytical
    imbalance_bending_pa = (
        32.0
        * results.imbalance_moment_n_m
        / (math.pi * results.inputs.shaft_diameter_m**3)
    )
    return {
        "name": scenario.name,
        "rpm": float(scenario.speed_rpm),
        "human_power_w": float(scenario.human_power_w),
        "fill_fraction": float(scenario.fill_fraction),
        "laundry_mass_kg": float(scenario.laundry_mass_kg),
        "eccentricity_m": float(scenario.eccentricity_m),
        "angular_speed_rad_s": float(analytical.angular_speed_rad_s),
        "imbalance_force_n": float(results.imbalance_force_n),
        "total_moment_n_m": float(results.total_moment_n_m),
        "von_mises_pa": float(results.von_mises_pa),
        "factor_of_safety": float(results.factor_of_safety),
        "water_mass_kg": float(analytical.retained_water_mass_kg),
        "drum_radius_m": float(results.inputs.drum_radius_m),
        "drum_depth_m": float(results.inputs.drum_depth_m),
        "shaft_static_bending_pa": float(analytical.shaft_bending_stress_pa),
        "shaft_imbalance_bending_pa": float(imbalance_bending_pa),
        "shaft_torsional_shear_pa": float(analytical.shaft_torsional_shear_pa),
        "hydrostatic_pressure_pa": float(analytical.hydrostatic_pressure_pa),
        "centrifugal_pressure_pa": float(analytical.centrifugal_pressure_pa),
        "design_pressure_pa": float(analytical.design_water_pressure_pa),
        "slosh_amplification": float(results.inputs.slosh_amplification),
        "provenance": report.provenance,
        "fea_provenance": report.fea_provenance,
    }


def _render_template(
    *,
    report_html: str,
    payload_json: str,
    three_bundle: str,
    selected_name: str,
    viewer_only: bool = False,
) -> str:
    try:
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"unable to read offline report template: {TEMPLATE_PATH}") from error
    replacements = {
        "@@REPORT_HTML@@": report_html,
        "@@PAYLOAD_JSON@@": payload_json,
        "@@THREE_BUNDLE@@": three_bundle,
        "@@SELECTED_SCENARIO@@": html.escape(selected_name, quote=True),
        "@@VIEWER_ONLY@@": "true" if viewer_only else "false",
    }
    for token, value in replacements.items():
        template = template.replace(token, value)
    if "@@" in template:
        raise RuntimeError("offline report template contains an unresolved token")
    return template


def _render_report(document: ReportDocument) -> str:
    scenario_rows = "".join(
        "<tr>"
        f"<th scope=\"row\">{_text(report.scenario.name)}</th>"
        f"<td>{report.scenario.speed_rpm:.0f} RPM</td>"
        f"<td>{report.scenario.human_power_w:.0f} W</td>"
        f"<td>{report.scenario.fill_fraction:.0%}</td>"
        f"<td>{report.results.imbalance_force_n:.1f} N</td>"
        f"<td>{report.results.factor_of_safety:.2f}</td>"
        "</tr>"
        for report in document.scenario_reports
    )
    formula_blocks = "".join(_formula_html(formula) for formula in core_formulas(document))
    selected = document.selected_report
    return f"""
<article class="technical-report">
  <header class="report-header">
    <p class="eyebrow">Offline technical evaluation</p>
    <h1>CycleWash Technical Evaluation</h1>
    <p>Selected scenario: <strong>{_text(selected.scenario.name)}</strong> at {selected.scenario.speed_rpm:.0f} RPM, with an analytical shaft factor of safety of {selected.results.factor_of_safety:.2f}.</p>
  </header>
  <section aria-labelledby="comparison-heading">
    <h2 id="comparison-heading">Scenario comparison</h2>
    <div class="table-wrap"><table><thead><tr><th>Scenario</th><th>Drum speed</th><th>Human power</th><th>Water fill</th><th>Imbalance</th><th>FoS</th></tr></thead><tbody>{scenario_rows}</tbody></table></div>
  </section>
  <section aria-labelledby="formula-heading">
    <h2 id="formula-heading">Core equations</h2>
    {formula_blocks}
  </section>
  <section aria-labelledby="limitations-heading"><h2 id="limitations-heading">Limitations</h2><p>{_text(LIMITATIONS_NOTE)}</p></section>
  <section aria-labelledby="conclusion-heading"><h2 id="conclusion-heading">Conclusion</h2><p>{_text(document.conclusion)}</p></section>
</article>"""


def _formula_html(formula: FormulaDefinition) -> str:
    return f"""
<section class="formula" aria-labelledby="formula-{_text(formula.identifier)}">
  <h3 id="formula-{_text(formula.identifier)}">{_text(formula.title)}</h3>
  <div class="equation" aria-label="{_text(formula.latex)}">{formula.html}</div>
  <div class="equation" aria-label="{_text(formula.evaluated_latex)}">{formula.evaluated_html}</div>
</section>"""


def _safe_json(value: Any) -> str:
    serialized = json.dumps(_json_value(value), allow_nan=False, separators=(",", ":"), sort_keys=True)
    return serialized.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, np.generic):
        return _json_value(value.item())
    if isinstance(value, float):
        if not np.isfinite(value):
            raise ValueError("offline report payload must contain finite numbers")
        return value
    return value


def _offline_three_bundle() -> str:
    try:
        bundle = THREE_BUNDLE_PATH.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise RuntimeError(f"unable to read pinned Three.js bundle: {THREE_BUNDLE_PATH}") from error
    return bundle


def _normalized_name(name: str) -> str:
    return " ".join(name.lower().replace("_", " ").replace("-", " ").split())


def _text(value: Any) -> str:
    return html.escape(str(value), quote=True)


__all__ = [
    "build_offline_report_html",
    "build_scenario_viewer_html",
    "viewer_asset_fingerprint",
]
