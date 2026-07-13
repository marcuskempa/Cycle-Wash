"""Compact geometry and field payloads for the CycleWash HTML animation.

Display STL coordinates remain unchanged in the payload.  Their explicitly
recorded scale is applied before comparing them with Stage 1 FEA coordinates,
which are always SI meters.
"""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, is_dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from types import MappingProxyType
from typing import Any, Final, Mapping, Sequence
import warnings

import numpy as np

from cyclewash_animation_model import build_animation_timeline, build_partial_cylinder_water_mesh
from cyclewash_engineering_model import (
    AnalyticalResults,
    EngineeringInputs,
    canonical_request_identity,
)
from cyclewash_fea_results import ComponentFieldResult, Stage1FeaPackage
from cyclewash_fea_mapping import map_fea_fields_to_stl
from cyclewash_structural_visualizer import (
    AssemblyPart,
    normalize_values,
    radial_torsion_stress,
    rotating_water_stress,
    stationary_water_stress,
)


SCHEMA_VERSION: Final[str] = "cyclewash-animation-v1"
RUNTIME_VERSION: Final[str] = "cyclewash-renderer-v2"
MAX_TYPED_ARRAY_BYTES: int = 64 * 1024 * 1024
MAX_INITIAL_PAYLOAD_BYTES: Final[int] = 20 * 1024 * 1024
FEA_MAPPING_WARNING_TOLERANCE_M: float = 0.010
FEA_MAPPING_MAX_PAIR_COUNT: int = 1_000_000
REGISTRATION_SCALE_WARNING_FRACTION: float = 0.10
PHASE_DEGREES: Final[tuple[float, ...]] = tuple(float(index * 30) for index in range(12))
ROTATION_AXIS: Final[tuple[float, float, float]] = (1.0, 0.0, 0.0)
SUPPORTED_COLORSCALES: Final[tuple[str, ...]] = ("Turbo", "Viridis", "Jet", "Rainbow")
GRAVITY_AXIS: Final[tuple[float, float, float]] = (0.0, 0.0, -1.0)
GEOMETRIC_DISCLAIMER: Final[str] = (
    "Normalized geometric teaching visualization; not physical stress and not FEA."
)
FEA_DISCLAIMER: Final[str] = (
    "Linear-static FEA with reduced-order water loading; not CFD."
)
TRANSIENT_QUALIFICATION: Final[str] = "Quasi-static phase samples; not transient FEA."
THREE_VERSION: Final[str] = "0.185.1"
MODULE_DIRECTORY: Final[Path] = Path(__file__).resolve().parent
TEMPLATE_PATH: Path = MODULE_DIRECTORY / "cyclewash_animation_template.html"
THREE_BUNDLE_PATH: Path = MODULE_DIRECTORY / "assets" / "cyclewash-three-bundle.min.js"
_TEMPLATE_TOKENS: Final[frozenset[str]] = frozenset(
    {"@@THREE_BUNDLE@@", "@@PAYLOAD_JSON@@"}
)
_TEMPLATE_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(r"@@[A-Z0-9_]+@@")

_DTYPES: Final[dict[str, np.dtype[Any]]] = {
    "float32": np.dtype("<f4"),
    "uint32": np.dtype("<u4"),
    "uint8": np.dtype("u1"),
}


def _validated_colorscale(colorscale: Any) -> str:
    if colorscale not in SUPPORTED_COLORSCALES:
        supported = ", ".join(SUPPORTED_COLORSCALES)
        raise ValueError(f"colorscale must be one of: {supported}")
    return str(colorscale)


def _normalized_rotation_axis(rotation_axis: Any) -> tuple[float, float, float]:
    try:
        axis = np.asarray(rotation_axis, dtype=float)
    except (TypeError, ValueError) as error:
        raise ValueError("rotation_axis must be a finite nonzero 3-vector") from error
    if axis.shape != (3,) or not np.isfinite(axis).all():
        raise ValueError("rotation_axis must be a finite nonzero 3-vector")
    magnitude = float(np.linalg.norm(axis))
    if not math.isfinite(magnitude) or magnitude <= np.finfo(float).eps:
        raise ValueError("rotation_axis must be a finite nonzero 3-vector")
    normalized = axis if abs(magnitude - 1.0) <= 1.0e-15 else axis / magnitude
    return tuple(float(value) for value in normalized)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class AnimationPartPayload:
    """One immutable display mesh with geometry stored once and fields separate."""

    name: str
    component_kind: str
    rotating: bool
    visible: bool
    color: str
    opacity: float
    geometry: Mapping[str, Any]
    fields: Mapping[str, Any]
    units: Mapping[str, Any]
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("part name must not be empty")
        if not 0.0 <= self.opacity <= 1.0:
            raise ValueError("part opacity must be between zero and one")
        for field_name in ("geometry", "fields", "units", "metadata"):
            object.__setattr__(self, field_name, _freeze(getattr(self, field_name)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "component_kind": self.component_kind,
            "rotating": self.rotating,
            "visible": self.visible,
            "color": self.color,
            "opacity": self.opacity,
            "geometry": _thaw(self.geometry),
            "fields": _thaw(self.fields),
            "units": _thaw(self.units),
            "metadata": _thaw(self.metadata),
        }


@dataclass(frozen=True)
class AnimationPayload:
    """Complete immutable, JSON-ready animation input without frame geometry copies."""

    schema_version: str
    parts: tuple[AnimationPartPayload, ...]
    rotation_axis: tuple[float, float, float]
    rotation_origin: tuple[float, float, float]
    water: Mapping[str, Any]
    timeline: tuple[Mapping[str, Any], ...]
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported animation schema: {self.schema_version!r}")
        parts = tuple(self.parts)
        if not all(isinstance(part, AnimationPartPayload) for part in parts):
            raise TypeError("parts must contain AnimationPartPayload instances")
        object.__setattr__(self, "parts", parts)
        object.__setattr__(self, "rotation_axis", _normalized_rotation_axis(self.rotation_axis))
        object.__setattr__(self, "rotation_origin", tuple(float(value) for value in self.rotation_origin))
        object.__setattr__(self, "water", _freeze(self.water))
        object.__setattr__(self, "timeline", tuple(_freeze(sample) for sample in self.timeline))
        object.__setattr__(self, "metadata", _freeze(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "parts": [part.to_dict() for part in self.parts],
            "rotation": {
                "axis": list(self.rotation_axis),
                "origin": list(self.rotation_origin),
            },
            "water": _thaw(self.water),
            "timeline": _thaw(self.timeline),
            "metadata": _thaw(self.metadata),
        }


def encode_typed_array(array: np.ndarray, dtype: str) -> str:
    """Return a safe base64 encoding in the requested browser typed-array format."""

    if dtype not in _DTYPES:
        supported = ", ".join(sorted(_DTYPES))
        raise ValueError(f"supported typed-array dtype required ({supported})")
    source = np.asarray(array)
    if not np.issubdtype(source.dtype, np.number) or np.issubdtype(source.dtype, np.complexfloating):
        raise ValueError("typed-array input must be a real numeric array")
    if not np.isfinite(source).all():
        raise ValueError("typed-array input must contain only finite values")
    target_dtype = _DTYPES[dtype]
    projected_nbytes = int(source.size) * int(target_dtype.itemsize)
    if projected_nbytes > MAX_TYPED_ARRAY_BYTES:
        raise ValueError(
            f"typed-array output exceeds the explicit {MAX_TYPED_ARRAY_BYTES}-byte safety ceiling"
        )
    if dtype.startswith("uint"):
        maximum = np.iinfo(target_dtype).max
        if np.any(source < 0) or np.any(source > maximum) or np.any(source != np.floor(source)):
            raise ValueError(f"typed-array input cannot be represented exactly as {dtype}")
    converted = np.ascontiguousarray(source, dtype=target_dtype)
    if not np.isfinite(converted).all():
        raise ValueError(f"typed-array input cannot be represented as finite {dtype}")
    if converted.nbytes > MAX_TYPED_ARRAY_BYTES:
        raise ValueError(
            f"typed-array output exceeds the explicit {MAX_TYPED_ARRAY_BYTES}-byte safety ceiling"
        )
    return base64.b64encode(converted.tobytes(order="C")).decode("ascii")


def _typed_array_record(array: np.ndarray, dtype: str) -> dict[str, Any]:
    source = np.asarray(array)
    return {
        "dtype": dtype,
        "shape": [int(value) for value in source.shape],
        "base64": encode_typed_array(source, dtype),
    }


def build_animation_payload(
    parts: Sequence[AssemblyPart],
    inputs: EngineeringInputs,
    analytical: AnalyticalResults,
    fea_package: Stage1FeaPackage | None = None,
    *,
    colorscale: str = "Turbo",
    rotation_axis: Sequence[float] = ROTATION_AXIS,
) -> AnimationPayload:
    """Build compact display geometry, water, timeline, and optional FEA fields."""

    part_list = tuple(parts)
    if not part_list:
        raise ValueError("parts must contain at least one AssemblyPart")
    if not all(isinstance(part, AssemblyPart) for part in part_list):
        raise TypeError("parts must contain only AssemblyPart instances")
    if not isinstance(inputs, EngineeringInputs):
        raise TypeError("inputs must be an EngineeringInputs instance")
    if not isinstance(analytical, AnalyticalResults):
        raise TypeError("analytical must be an AnalyticalResults instance")
    if fea_package is not None and not isinstance(fea_package, Stage1FeaPackage):
        raise TypeError("fea_package must be a Stage1FeaPackage or None")
    selected_colorscale = _validated_colorscale(colorscale)
    normalized_rotation_axis = _normalized_rotation_axis(rotation_axis)

    scale_to_si, unit_metadata = _infer_display_scale_to_si(part_list, inputs)
    rotation_origin = _infer_rotating_axis_origin(part_list)
    fea_provenance = (
        _verify_fea_provenance(inputs, analytical, fea_package)
        if fea_package is not None
        else None
    )
    payload_parts = tuple(
        _build_part_payload(
            part,
            inputs,
            rotation_origin,
            scale_to_si,
            unit_metadata,
            fea_package,
            normalized_rotation_axis,
        )
        for part in part_list
    )
    water = _build_water_payload(
        part_list,
        inputs,
        analytical,
        rotation_origin,
        scale_to_si,
        normalized_rotation_axis,
    )
    timeline = _build_timeline_payload(inputs, analytical)
    analysis_mode = "fea" if fea_package is not None else "geometric"
    metadata = {
        "analysis_mode": analysis_mode,
        "scope_disclaimer": FEA_DISCLAIMER if fea_package is not None else GEOMETRIC_DISCLAIMER,
        "inputs": _json_value(inputs),
        "analytical": _json_value(analytical),
        "phase_degrees": list(PHASE_DEGREES),
        "pressure_values_pa": {
            "hydrostatic": float(analytical.hydrostatic_pressure_pa),
            "centrifugal": float(analytical.centrifugal_pressure_pa),
            "design": float(analytical.design_water_pressure_pa),
        },
        "water_values": {
            "fill_fraction": float(inputs.fill_fraction),
            "perforation_relief": float(inputs.perforation_relief),
            "retained_mass_kg": float(analytical.retained_water_mass_kg),
            "retained_volume_m3": float(analytical.retained_water_volume_m3),
        },
        "display_units": unit_metadata,
        "display_options": {
            "colorscale": selected_colorscale,
            "rotation_axis": list(normalized_rotation_axis),
        },
        "runtime_version": RUNTIME_VERSION,
        "three_version": THREE_VERSION,
    }
    if fea_package is not None:
        metadata["transient_qualification"] = TRANSIENT_QUALIFICATION
        metadata["fea"] = {
            "schema_version": fea_package.schema_version,
            "solver_versions": dict(fea_package.solver_versions),
            "assumptions": dict(fea_package.assumptions),
            "provenance": fea_provenance,
        }
    # Validate JSON compatibility at the producer boundary, before HTML export.
    json.dumps(_thaw(_freeze(metadata)), sort_keys=True, allow_nan=False)
    return AnimationPayload(
        schema_version=SCHEMA_VERSION,
        parts=payload_parts,
        rotation_axis=normalized_rotation_axis,
        rotation_origin=rotation_origin,
        water=water,
        timeline=timeline,
        metadata=metadata,
    )


def export_cyclewash_animation_html(
    payload: AnimationPayload | Mapping[str, Any], output_path: str | Path
) -> Path:
    """Write one deterministic, self-contained animation document atomically."""

    if isinstance(payload, AnimationPayload):
        document = payload.to_dict()
    elif isinstance(payload, Mapping):
        document = _json_value(payload)
    else:
        raise TypeError("payload must be an AnimationPayload or mapping")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported animation schema: {document.get('schema_version')!r}")
    if not isinstance(document.get("parts"), list):
        raise ValueError("animation payload must contain a parts list")

    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("animation payload must contain metadata")
    metadata["three_version"] = THREE_VERSION

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    template_tokens = _TEMPLATE_TOKEN_PATTERN.findall(template)
    unknown_tokens = set(template_tokens) - _TEMPLATE_TOKENS
    invalid_counts = {
        token: template_tokens.count(token)
        for token in _TEMPLATE_TOKENS
        if template_tokens.count(token) != 1
    }
    if unknown_tokens or invalid_counts:
        details = []
        if unknown_tokens:
            details.append(f"unknown {sorted(unknown_tokens)}")
        if invalid_counts:
            details.append(f"required token counts {invalid_counts}")
        raise ValueError(f"template token validation failed: {'; '.join(details)}")

    bundle = THREE_BUNDLE_PATH.read_text(encoding="utf-8")
    if THREE_VERSION not in bundle or "@license" not in bundle:
        raise ValueError("Three.js bundle is missing pinned version or license metadata")
    bundle = re.sub(r"</script", r"<\\/script", bundle, flags=re.IGNORECASE)
    payload_json = _safe_inline_json(document)
    html = template.replace("@@THREE_BUNDLE@@", bundle).replace(
        "@@PAYLOAD_JSON@@", payload_json
    )
    unresolved = _TEMPLATE_TOKEN_PATTERN.findall(html)
    if unresolved:
        raise ValueError(f"template token replacement left unresolved tokens: {unresolved}")
    if html.count("window.CYCLEWASH_ANIMATION =") != 1:
        raise ValueError("template must contain exactly one payload assignment")

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(html)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return destination


def renderer_asset_fingerprint(
    *,
    exporter_path: str | Path | None = None,
    template_path: str | Path | None = None,
    bundle_path: str | Path | None = None,
    schema_version: str = SCHEMA_VERSION,
    runtime_version: str = RUNTIME_VERSION,
) -> str:
    """Hash exact renderer assets and contract versions for cache invalidation."""

    sources = (
        ("schema_version", str(schema_version).encode("utf-8")),
        ("runtime_version", str(runtime_version).encode("utf-8")),
        ("exporter", Path(exporter_path or __file__).resolve().read_bytes()),
        ("template", Path(template_path or TEMPLATE_PATH).resolve().read_bytes()),
        ("bundle", Path(bundle_path or THREE_BUNDLE_PATH).resolve().read_bytes()),
    )
    digest = hashlib.sha256()
    for label, content in sources:
        label_bytes = label.encode("ascii")
        digest.update(len(label_bytes).to_bytes(4, "big"))
        digest.update(label_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _safe_inline_json(value: Any) -> str:
    serialized = json.dumps(
        _json_value(value),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return (
        serialized.replace("&", r"\u0026")
        .replace("<", r"\u003c")
        .replace(">", r"\u003e")
    )


def _verify_fea_provenance(
    inputs: EngineeringInputs,
    analytical: AnalyticalResults,
    package: Stage1FeaPackage,
) -> dict[str, Any]:
    if not package.inputs:
        raise ValueError("FEA package must contain non-empty stored inputs")
    expected_inputs = _json_value(inputs)
    if _canonical_json(package.inputs) != _canonical_json(expected_inputs):
        raise ValueError("FEA package stored inputs do not match supplied EngineeringInputs")

    if not package.analytical_values:
        raise ValueError("FEA package must contain non-empty stored analytical_values")
    expected_analytical = _json_value(analytical)
    if _canonical_json(package.analytical_values) != _canonical_json(expected_analytical):
        raise ValueError(
            "FEA package stored analytical_values do not match supplied AnalyticalResults"
        )

    stored_identity = package.assumptions.get("request_identity")
    if not isinstance(stored_identity, Mapping) or not stored_identity:
        raise ValueError("FEA package must contain a non-empty request identity")
    mesh_levels = stored_identity.get("mesh_levels")
    if not isinstance(mesh_levels, (list, tuple)) or not mesh_levels:
        raise ValueError("FEA package request identity must contain mesh_levels")
    expected_identity = canonical_request_identity(inputs, mesh_levels)
    if _canonical_json(stored_identity) != _canonical_json(expected_identity):
        raise ValueError("FEA package request identity does not match supplied inputs and mesh levels")

    mesh_level = package.assumptions.get("mesh_level")
    if mesh_level is not None and mesh_level not in expected_identity["mesh_levels"]:
        raise ValueError("FEA package mesh_level is inconsistent with its request identity")
    component_mesh = {}
    for name in ("shaft", "gear", "drum"):
        component = getattr(package, name)
        convergence = package.convergence.get(name)
        component_mesh[name] = {
            "node_count": int(component.vertices_m.shape[0]),
            "element_count": int(component.tetrahedra.shape[0]),
            "phase_count": int(component.von_mises_pa.shape[0]),
            "convergence_status": (
                "not_evaluated"
                if convergence is None
                else ("converged" if convergence.converged else "unresolved_sensitivity")
            ),
        }
    return {
        "inputs_verified": True,
        "analytical_values_verified": True,
        "request_identity_verified": True,
        "request_identity": expected_identity,
        "mesh_levels": list(expected_identity["mesh_levels"]),
        "mesh_level": mesh_level,
        "components": component_mesh,
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_value(value),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _build_part_payload(
    part: AssemblyPart,
    inputs: EngineeringInputs,
    rotation_origin: tuple[float, float, float],
    scale_to_si: float,
    unit_metadata: Mapping[str, Any],
    fea_package: Stage1FeaPackage | None,
    rotation_axis: tuple[float, float, float],
) -> AnimationPartPayload:
    vertices = np.asarray(part.vertices, dtype=float)
    triangles = np.asarray(part.faces)
    if vertices.ndim != 2 or vertices.shape[1:] != (3,) or len(vertices) == 0:
        raise ValueError(f"part {part.name!r} vertices must have shape (n, 3)")
    if triangles.ndim != 2 or triangles.shape[1:] != (3,):
        raise ValueError(f"part {part.name!r} triangles must have shape (n, 3)")
    if not np.issubdtype(triangles.dtype, np.integer):
        raise ValueError(f"part {part.name!r} triangles must contain integer indices")
    if triangles.size and (triangles.min() < 0 or triangles.max() >= len(vertices)):
        raise ValueError(f"part {part.name!r} triangles contain an invalid vertex index")

    normalized_name = _normalize_name(part.name)
    rotating = _is_rotating(normalized_name)
    component_name = _fea_component_name(normalized_name)
    enclosure = "enclosure" in normalized_name
    metadata: dict[str, Any] = {}
    if enclosure:
        fields = {}
    elif fea_package is not None and component_name is not None:
        fields, mapping_metadata = _fea_fields(
            vertices, scale_to_si, getattr(fea_package, component_name), component_name, part.name
        )
        metadata["fea_mapping"] = mapping_metadata
    else:
        fields = _geometric_fields(part, inputs, rotation_origin, rotating, rotation_axis)

    return AnimationPartPayload(
        name=part.name,
        component_kind=part.component_kind,
        rotating=rotating,
        visible=True,
        color="#9ca3af" if enclosure else _material_color(part, normalized_name),
        opacity=0.5 if enclosure else 1.0,
        geometry={
            "vertices": _typed_array_record(vertices, "float32"),
            "triangles": _typed_array_record(triangles, "uint32"),
        },
        fields=fields,
        units={
            "source_unit": unit_metadata["source_unit"],
            "scale_to_si": scale_to_si,
            "si_unit": "m",
        },
        metadata=metadata,
    )


def _geometric_fields(
    part: AssemblyPart,
    inputs: EngineeringInputs,
    rotation_origin: tuple[float, float, float],
    rotating: bool,
    rotation_axis: tuple[float, float, float],
) -> dict[str, Any]:
    normalized_name = _normalize_name(part.name)
    phases: list[dict[str, Any]] = []
    for phase in PHASE_DEGREES:
        if "shaft" in normalized_name or "gear" in normalized_name:
            values = radial_torsion_stress(
                part, axis_vector=rotation_axis, axis_origin=rotation_origin
            )
        elif rotating:
            values = rotating_water_stress(
                part,
                rotation_axis=rotation_axis,
                gravity_axis=GRAVITY_AXIS,
                axis_origin=rotation_origin,
                rpm=inputs.speed_rpm,
                rotation_angle_degrees=phase,
                water_fill_fraction=inputs.fill_fraction,
                perforation_relief=inputs.perforation_relief,
            )
        else:
            values = stationary_water_stress(
                part,
                gravity_axis=GRAVITY_AXIS,
                water_fill_fraction=inputs.fill_fraction,
                perforation_relief=inputs.perforation_relief,
            )
        quantized = np.rint(normalize_values(values) * 255.0).astype(np.uint8)
        phases.append(_typed_array_record(quantized, "uint8"))
    return {
        "stress": {
            "source": "geometric_preview",
            "physical_stress": False,
            "normalization": "independent normalized geometric phase",
            "phases": phases,
        }
    }


def _fea_fields(
    display_vertices: np.ndarray,
    scale_to_si: float,
    component: ComponentFieldResult,
    component_name: str,
    part_name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    display_si = np.asarray(display_vertices, dtype=np.float64) * scale_to_si
    mapping_part = AssemblyPart(
        name=part_name,
        local_vertices=display_si,
        faces=np.asarray([[0, 1, 2]], dtype=np.int64),
        component_kind=component_name,
    )
    mapped = map_fea_fields_to_stl(mapping_part, component, component_name)
    stats = mapped.metadata["projection_error_m"]
    registration = mapped.metadata["registration"]
    mesh_scale = mapped.metadata["solver_mesh_scale_m"]
    mapping_warning = stats["p95"] > FEA_MAPPING_WARNING_TOLERANCE_M
    warning = mapping_warning or registration["warning"]
    if warning:
        reasons = []
        if registration["warning"]:
            reasons.append(registration["warning_message"])
        if mapping_warning:
            reasons.append(
                f"post-registration P95 {stats['p95']:.6g} m exceeds "
                f"tolerance {FEA_MAPPING_WARNING_TOLERANCE_M:.6g} m"
            )
        warnings.warn(
            f"FEA visualization transfer warning for {part_name!r}: {'; '.join(reasons)}",
            RuntimeWarning,
            stacklevel=3,
        )

    source_stress = np.asarray(component.von_mises_pa, dtype=np.float64)
    source_displacement = np.asarray(component.displacement_m, dtype=np.float64)
    stress_min = float(np.min(source_stress))
    stress_max = float(np.max(source_stress))
    stress_span = stress_max - stress_min
    stress_records: list[dict[str, Any]] = []
    displacement_records: list[dict[str, Any]] = []
    phase_stress_maxima: list[float] = []
    phase_displacement_maxima: list[float] = []
    for output_phase in range(12):
        source_phase = output_phase if source_stress.shape[0] == 12 else 0
        mapped_stress = mapped.stress_pa[source_phase]
        normalized = (
            np.zeros_like(mapped_stress)
            if stress_span == 0.0
            else (mapped_stress - stress_min) / stress_span
        )
        stress_records.append(
            _typed_array_record(np.rint(np.clip(normalized, 0.0, 1.0) * 255.0), "uint8")
        )
        mapped_displacement = mapped.displacement_m[source_phase]
        displacement_records.append(_typed_array_record(mapped_displacement, "float32"))
        phase_stress_maxima.append(float(np.max(source_stress[source_phase])))
        phase_displacement_maxima.append(
            float(np.max(np.linalg.norm(source_displacement[source_phase], axis=1)))
        )
    displacement_magnitude = np.linalg.norm(source_displacement, axis=2)
    fields = {
        "stress": {
            "source": "stage1_fea",
            "physical_stress": True,
            "display_encoding": "uint8 normalized over physical_range_pa",
            "physical_range_pa": [stress_min, stress_max],
            "phase_maximum_pa": phase_stress_maxima,
            "phases": stress_records,
        },
        "displacement": {
            "source": "stage1_fea",
            "units": "m",
            "vectors_scaled_by_registration": False,
            "physical_range_m": [
                float(np.min(displacement_magnitude)),
                float(np.max(displacement_magnitude)),
            ],
            "phase_maximum_m": phase_displacement_maxima,
            "phases": displacement_records,
            "visual_deformation": {
                "applied": False,
                "optional": True,
                "reason": (
                    "Direction is ambiguous after anisotropic visualization registration; "
                    "stored vectors remain physical solver-coordinate meters."
                    if registration["material_anisotropy"]
                    else "Stored vectors remain physical solver-coordinate meters and are not applied."
                ),
            },
        },
    }
    mapping_metadata = {
        "component": component_name,
        "display_scale_to_si": scale_to_si,
        "registration": registration,
        "pre_registration_mismatch": mapped.metadata["pre_registration_mismatch"],
        "error_m": stats,
        "solver_mesh_scale_m": mesh_scale,
        "p95_error_over_p95_edge": stats["p95"] / mesh_scale["p95_edge"],
        "warning_tolerance_m": FEA_MAPPING_WARNING_TOLERANCE_M,
        "mapping_warning": mapping_warning,
        "warning": warning,
        "solver_surface_node_count": mapped.metadata["solver_surface_node_count"],
        "solver_projection_node_count": mapped.metadata["solver_projection_node_count"],
        "projection_domain": mapped.metadata["projection_domain"],
        "method": "chunked nearest-surface-node projection after explicit component registration",
    }
    return fields, mapping_metadata


def _register_display_to_solver(
    display_si: np.ndarray,
    solver_vertices: np.ndarray,
    component_name: str,
) -> tuple[np.ndarray, dict[str, Any]]:
    source_raw_min = display_si.min(axis=0)
    source_raw_max = display_si.max(axis=0)
    source_min, source_max = _robust_bounds(display_si)
    target_min = solver_vertices.min(axis=0)
    target_max = solver_vertices.max(axis=0)
    source_span = source_max - source_min
    target_span = target_max - target_min
    if np.any(source_span <= 0.0) or np.any(target_span <= 0.0):
        raise ValueError(f"{component_name} registration requires positive bounds on every axis")

    source_center = (source_min + source_max) / 2.0
    target_center = (target_min + target_max) / 2.0
    scale_factors = target_span / source_span
    translation = target_center - source_center * scale_factors
    registered = display_si * scale_factors + translation

    mismatch = np.abs(scale_factors - 1.0)
    anisotropy_ratio = float(np.max(scale_factors) / np.min(scale_factors))
    radial_anisotropy_ratio = float(
        max(scale_factors[1], scale_factors[2]) / min(scale_factors[1], scale_factors[2])
    )
    material_anisotropy = (
        anisotropy_ratio - 1.0 > REGISTRATION_SCALE_WARNING_FRACTION
        or radial_anisotropy_ratio - 1.0 > REGISTRATION_SCALE_WARNING_FRACTION
    )
    warning = bool(
        np.any(mismatch > REGISTRATION_SCALE_WARNING_FRACTION) or material_anisotropy
    )
    warning_message = (
        "Visualization registration materially rescales/distorts CAD coordinates; "
        "this is not physical geometric agreement."
        if warning
        else "Visualization registration is not evidence of physical geometric agreement."
    )
    metadata = {
        "method": (
            "Component-axis affine registration for visualization field transfer; "
            "not physical geometric agreement."
        ),
        "component": component_name,
        "longitudinal_axis": "Blender X",
        "translation_m": [float(value) for value in translation],
        "longitudinal_scale_factor": float(scale_factors[0]),
        "radial_scale_factors": {
            "y": float(scale_factors[1]),
            "z": float(scale_factors[2]),
        },
        "source_bounds_m": {
            "raw_min": [float(value) for value in source_raw_min],
            "raw_max": [float(value) for value in source_raw_max],
            "registration_min": [float(value) for value in source_min],
            "registration_max": [float(value) for value in source_max],
        },
        "target_solver_bounds_m": {
            "min": [float(value) for value in target_min],
            "max": [float(value) for value in target_max],
        },
        "scale_mismatch": {
            "longitudinal_fraction": float(mismatch[0]),
            "radial_y_fraction": float(mismatch[1]),
            "radial_z_fraction": float(mismatch[2]),
            "maximum_fraction": float(np.max(mismatch)),
            "anisotropy_ratio": anisotropy_ratio,
            "radial_anisotropy_ratio": radial_anisotropy_ratio,
        },
        "material_anisotropy": material_anisotropy,
        "warning_threshold_fraction": REGISTRATION_SCALE_WARNING_FRACTION,
        "warning": warning,
        "warning_message": warning_message,
    }
    return registered, metadata


def _robust_bounds(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if len(points) < 128:
        return points.min(axis=0), points.max(axis=0)
    lower = np.quantile(points, 0.001, axis=0)
    upper = np.quantile(points, 0.999, axis=0)
    if np.any(upper <= lower):
        return points.min(axis=0), points.max(axis=0)
    return lower, upper


def _error_statistics(errors: np.ndarray) -> dict[str, float]:
    return {
        "median": float(np.median(errors)),
        "p95": float(np.percentile(errors, 95)),
        "max": float(np.max(errors)),
    }


def _tetrahedral_edge_scale(
    vertices: np.ndarray, tetrahedra: np.ndarray
) -> dict[str, float]:
    tetrahedra = np.asarray(tetrahedra, dtype=np.int64)
    edge_pairs = np.concatenate(
        tuple(
            tetrahedra[:, pair]
            for pair in ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))
        ),
        axis=0,
    )
    edge_lengths = np.linalg.norm(
        np.asarray(vertices)[edge_pairs[:, 0]] - np.asarray(vertices)[edge_pairs[:, 1]],
        axis=1,
    )
    if not len(edge_lengths) or np.any(edge_lengths <= 0.0):
        raise ValueError("FEA tetrahedra must have positive edge lengths")
    return {
        "median_edge": float(np.median(edge_lengths)),
        "p95_edge": float(np.percentile(edge_lengths, 95)),
        "maximum_edge": float(np.max(edge_lengths)),
    }


def _tetrahedral_surface_nodes(tetrahedra: np.ndarray) -> np.ndarray:
    tetrahedra = np.asarray(tetrahedra, dtype=np.int64)
    if tetrahedra.ndim != 2 or tetrahedra.shape[1:] != (4,) or len(tetrahedra) == 0:
        raise ValueError("FEA tetrahedra must have shape (element_count, 4) and be non-empty")
    faces = np.concatenate(
        (
            tetrahedra[:, (1, 2, 3)],
            tetrahedra[:, (0, 3, 2)],
            tetrahedra[:, (0, 1, 3)],
            tetrahedra[:, (0, 2, 1)],
        ),
        axis=0,
    )
    sorted_faces = np.sort(faces, axis=1)
    unique_faces, counts = np.unique(sorted_faces, axis=0, return_counts=True)
    boundary_faces = unique_faces[counts == 1]
    if boundary_faces.size == 0:
        raise ValueError("FEA tetrahedra do not contain a boundary surface")
    return np.unique(boundary_faces)


def _chunked_nearest_indices(
    query_points: np.ndarray, reference_points: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    if len(query_points) == 0 or len(reference_points) == 0:
        raise ValueError("nearest projection requires non-empty point arrays")
    max_pairs = max(1, int(FEA_MAPPING_MAX_PAIR_COUNT))
    query_chunk_size = max(1, min(len(query_points), int(math.sqrt(max_pairs))))
    nearest = np.empty(len(query_points), dtype=np.int64)
    best_distances = np.empty(len(query_points), dtype=np.float64)
    reference_norm = np.einsum("ij,ij->i", reference_points, reference_points)
    for query_start in range(0, len(query_points), query_chunk_size):
        query_stop = min(query_start + query_chunk_size, len(query_points))
        query_chunk = query_points[query_start:query_stop]
        reference_chunk_size = max(1, max_pairs // len(query_chunk))
        local_best = np.full(len(query_chunk), np.inf, dtype=np.float64)
        local_indices = np.zeros(len(query_chunk), dtype=np.int64)
        query_norm = np.einsum("ij,ij->i", query_chunk, query_chunk)[:, None]
        for reference_start in range(0, len(reference_points), reference_chunk_size):
            reference_stop = min(reference_start + reference_chunk_size, len(reference_points))
            candidate = reference_points[reference_start:reference_stop]
            squared = query_norm + reference_norm[None, reference_start:reference_stop]
            squared -= 2.0 * (query_chunk @ candidate.T)
            np.maximum(squared, 0.0, out=squared)
            candidate_indices = np.argmin(squared, axis=1)
            candidate_distances = squared[np.arange(len(query_chunk)), candidate_indices]
            improved = candidate_distances < local_best
            local_best[improved] = candidate_distances[improved]
            local_indices[improved] = reference_start + candidate_indices[improved]
        nearest[query_start:query_stop] = local_indices
        best_distances[query_start:query_stop] = np.sqrt(local_best)
    return nearest, best_distances


def _build_water_payload(
    parts: Sequence[AssemblyPart],
    inputs: EngineeringInputs,
    analytical: AnalyticalResults,
    rotation_origin: tuple[float, float, float],
    scale_to_si: float,
    rotation_axis: tuple[float, float, float],
) -> dict[str, Any]:
    drum_parts = [
        part
        for part in parts
        if "inner drum" in _normalize_name(part.name) or _normalize_name(part.name) == "drum"
    ]
    center_display = np.asarray(rotation_origin, dtype=float)
    axis = np.asarray(rotation_axis, dtype=float)
    center_projection = float(np.dot(center_display, axis))
    default_x_axis = rotation_axis == ROTATION_AXIS
    requested_length_source = inputs.drum_depth_m / scale_to_si
    if drum_parts:
        drum_vertices = np.vstack([np.asarray(part.vertices, dtype=float) for part in drum_parts])
        drum_projection = drum_vertices @ axis
        drum_projection_min = float(np.min(drum_projection))
        drum_projection_max = float(np.max(drum_projection))
        available_length_source = drum_projection_max - drum_projection_min
        visual_length_source = min(requested_length_source, available_length_source)
        start_projection = (
            drum_projection_min + drum_projection_max - visual_length_source
        ) / 2.0
        longitudinal_source = (
            "inner drum display X bounds"
            if default_x_axis
            else "inner drum display rotation-axis projection bounds"
        )
        strategy = (
            "centered exact engineering depth within drum bounds"
            if requested_length_source <= available_length_source
            else (
                "clamped to available drum display X span"
                if default_x_axis
                else "clamped to available drum rotation-axis projection span"
            )
        )
    else:
        visual_length_source = requested_length_source
        start_projection = center_projection - visual_length_source / 2.0
        drum_projection_min = start_projection
        drum_projection_max = start_projection + visual_length_source
        longitudinal_source = (
            "shaft X center fallback"
            if default_x_axis
            else "shaft center rotation-axis projection fallback"
        )
        strategy = "centered engineering depth on shaft fallback"
    if default_x_axis:
        start_display = np.asarray(
            [start_projection, center_display[1], center_display[2]], dtype=float
        )
        centerline_source = "shaft display bounds Y/Z center"
    else:
        start_display = center_display + (start_projection - center_projection) * axis
        centerline_source = "shaft display center perpendicular to rotation axis"
    start_si = start_display * scale_to_si
    visual_length_m = visual_length_source * scale_to_si
    mesh = build_partial_cylinder_water_mesh(
        axis_origin=start_si,
        axis=axis,
        gravity_axis=GRAVITY_AXIS,
        radius=inputs.drum_radius_m,
        length=visual_length_m,
        fill_fraction=inputs.fill_fraction,
        segments=48,
    )
    placement = {
        "longitudinal_source": longitudinal_source,
        "centerline_source": centerline_source,
        "strategy": strategy,
        "drum_axis_projection_bounds_source_units": [
            drum_projection_min,
            drum_projection_max,
        ],
        "start_axis_projection_source_units": float(start_projection),
        "end_axis_projection_source_units": float(
            start_projection + visual_length_source
        ),
    }
    if default_x_axis:
        placement.update(
            {
                "drum_x_bounds_source_units": [
                    drum_projection_min,
                    drum_projection_max,
                ],
                "start_x_source_units": float(start_projection),
                "end_x_source_units": float(start_projection + visual_length_source),
            }
        )
    return {
        "axis": list(rotation_axis),
        "axis_origin": [float(value) for value in start_display],
        "gravity_axis": list(GRAVITY_AXIS),
        "fill_fraction": float(inputs.fill_fraction),
        "radius_m": float(inputs.drum_radius_m),
        "length_m": float(visual_length_m),
        "requested_length_m": float(inputs.drum_depth_m),
        "scale_to_si": scale_to_si,
        "placement": placement,
        "pressure_scale_pa": float(analytical.design_water_pressure_pa),
        "model_disclaimer": "Reduced-order gravity-level water visualization; not CFD.",
        "vertices": _typed_array_record(mesh.vertices / scale_to_si, "float32"),
        "triangles": _typed_array_record(mesh.triangles, "uint32"),
        "surface_vertex_indices": _typed_array_record(mesh.surface_vertex_indices, "uint32"),
    }


def _build_timeline_payload(
    inputs: EngineeringInputs, analytical: AnalyticalResults
) -> tuple[dict[str, float], ...]:
    period_s = 60.0 / inputs.speed_rpm
    samples = build_animation_timeline(
        rpm=inputs.speed_rpm,
        duration_s=period_s * 11.0 / 12.0,
        sample_count=12,
        slosh_amplitude_deg=8.0,
        pressure_scale_pa=analytical.design_water_pressure_pa,
    )
    return tuple(
        {
            "time_s": float(sample.time_s),
            "phase_deg": float(sample.phase_deg),
            "slosh_angle_deg": float(sample.slosh_angle_deg),
            "pressure_scale_pa": float(sample.pressure_scale_pa),
        }
        for sample in samples
    )


def _infer_rotating_axis_origin(parts: Sequence[AssemblyPart]) -> tuple[float, float, float]:
    shaft_parts = [part for part in parts if "shaft" in _normalize_name(part.name)]
    source = shaft_parts or [part for part in parts if _is_rotating(_normalize_name(part.name))]
    if not source:
        return (0.0, 0.0, 0.0)
    vertices = np.vstack([np.asarray(part.vertices, dtype=float) for part in source])
    if shaft_parts:
        origin = (vertices.min(axis=0) + vertices.max(axis=0)) / 2.0
    else:
        origin = vertices.mean(axis=0)
    return tuple(float(value) for value in origin)


def _infer_display_scale_to_si(
    parts: Sequence[AssemblyPart], inputs: EngineeringInputs
) -> tuple[float, dict[str, Any]]:
    vertices = np.vstack([np.asarray(part.vertices, dtype=float) for part in parts])
    if not np.isfinite(vertices).all():
        raise ValueError("display STL vertices must contain only finite values")
    observed_span = float(np.max(np.ptp(vertices, axis=0)))
    if observed_span <= 0.0:
        raise ValueError("display STL assembly must have a positive spatial span")
    reference_span = max(
        inputs.enclosure_width_m,
        inputs.overall_height_m,
        2.0 * inputs.drum_radius_m,
        inputs.drum_depth_m,
    )
    semantic_dimensions = _semantic_dimension_diagnostics(parts, inputs)
    candidates = {"m": 1.0, "mm": 0.001}
    scores: dict[str, float] = {}
    for unit, scale in candidates.items():
        dimension_errors = [
            abs(math.log10(item["observed_source_units"] * scale / item["expected_m"]))
            for item in semantic_dimensions
        ]
        assembly_error = abs(math.log10(observed_span * scale / reference_span))
        scores[unit] = float(
            np.mean(dimension_errors) if dimension_errors else assembly_error
        )
    source_unit = min(scores, key=scores.get)
    scale = candidates[source_unit]
    implied_scales = [item["expected_m"] / item["observed_source_units"] for item in semantic_dimensions]
    dimension_uniform_scale = (
        float(np.exp(np.mean(np.log(implied_scales)))) if implied_scales else scale
    )
    dimension_scale_ratio = (
        float(max(implied_scales) / min(implied_scales)) if implied_scales else 1.0
    )
    recorded_dimensions = [
        {
            **item,
            "implied_scale_to_si": item["expected_m"] / item["observed_source_units"],
            "ratio_at_inferred_scale": item["observed_source_units"] * scale / item["expected_m"],
        }
        for item in semantic_dimensions
    ]
    return scale, {
        "source_unit": source_unit,
        "scale_to_si": scale,
        "si_unit": "m",
        "inference": (
            "lowest mean log error across semantic drum/shaft dimensions; "
            "assembly span fallback only when semantic parts are absent"
        ),
        "observed_assembly_span_source_units": observed_span,
        "reference_span_m": float(reference_span),
        "candidate_log10_errors": scores,
        "semantic_dimensions": recorded_dimensions,
        "dimension_derived_uniform_scale_to_si": dimension_uniform_scale,
        "dimension_scale_max_to_min_ratio": dimension_scale_ratio,
        "dimension_uniform_scale_is_diagnostic_only": True,
    }


def _semantic_dimension_diagnostics(
    parts: Sequence[AssemblyPart], inputs: EngineeringInputs
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for part in parts:
        normalized_name = _normalize_name(part.name)
        if "shaft" in normalized_name:
            expected = (inputs.shaft_length_m, inputs.shaft_diameter_m, inputs.shaft_diameter_m)
            component = "shaft"
        elif "inner drum" in normalized_name or normalized_name == "drum":
            expected = (inputs.drum_depth_m, 2.0 * inputs.drum_radius_m, 2.0 * inputs.drum_radius_m)
            component = "drum"
        else:
            continue
        lower, upper = _robust_bounds(np.asarray(part.vertices, dtype=np.float64))
        span = upper - lower
        for axis_index, axis_name in enumerate(("longitudinal_x", "radial_y", "radial_z")):
            if span[axis_index] <= 0.0:
                continue
            diagnostics.append(
                {
                    "component": component,
                    "part_name": part.name,
                    "axis": axis_name,
                    "observed_source_units": float(span[axis_index]),
                    "expected_m": float(expected[axis_index]),
                }
            )
    return diagnostics


def _normalize_name(name: str) -> str:
    return " ".join(name.lower().replace("-", " ").replace("_", " ").replace(".", " ").split())


def _is_rotating(normalized_name: str) -> bool:
    return any(token in normalized_name for token in ("inner drum", "drum", "agitator", "gear", "shaft"))


def _fea_component_name(normalized_name: str) -> str | None:
    if "shaft" in normalized_name:
        return "shaft"
    if "gear" in normalized_name:
        return "gear"
    if "inner drum" in normalized_name or normalized_name == "drum":
        return "drum"
    return None


def _material_color(part: AssemblyPart, normalized_name: str) -> str:
    inferred = (
        ("shaft", "#d97706"),
        ("gear", "#f59e0b"),
        ("inner drum", "#60a5fa"),
        ("drum", "#60a5fa"),
        ("agitator", "#38bdf8"),
        ("door", "#64748b"),
        ("dampener", "#7c3aed"),
        ("damper", "#7c3aed"),
        ("foot", "#475569"),
    )
    if part.material_color and part.material_color.lower() != "#9ca3af":
        return part.material_color
    return next((color for token, color in inferred if token in normalized_name), "#94a3b8")


def _json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, np.generic):
        return _json_value(value.item())
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("payload metadata must contain only finite numbers")
        return value
    raise TypeError(f"payload metadata value is not JSON-compatible: {type(value).__name__}")


__all__ = [
    "AnimationPartPayload",
    "AnimationPayload",
    "FEA_MAPPING_WARNING_TOLERANCE_M",
    "MAX_INITIAL_PAYLOAD_BYTES",
    "MAX_TYPED_ARRAY_BYTES",
    "RUNTIME_VERSION",
    "SUPPORTED_COLORSCALES",
    "THREE_VERSION",
    "build_animation_payload",
    "encode_typed_array",
    "export_cyclewash_animation_html",
    "renderer_asset_fingerprint",
]
