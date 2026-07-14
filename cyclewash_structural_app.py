"""Sample Streamlit structural load/stress visualizer for CycleWash STL parts."""

from __future__ import annotations

import hashlib
import json
import math
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np


CURRENT_DIR = Path(__file__).resolve().parent
DEFAULT_STL_DIRECTORY = CURRENT_DIR
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

try:
    import plotly.graph_objects as go
    import streamlit as st
except ImportError as error:
    missing_name = getattr(error, "name", "streamlit or plotly")
    raise SystemExit(
        f"Missing dependency: {missing_name}. Install GUI dependencies with: "
        "python -m pip install -r requirements.txt"
    ) from error

from cyclewash_structural_visualizer import (
    AssemblyPart,
    StlPartSpec,
    Transform,
    hinge_lever_stress,
    load_stl_part,
    mesh3d_kwargs,
    radial_torsion_stress,
    rotating_water_stress,
    stationary_water_stress,
)
from cyclewash_engineering_model import (
    AnalyticalResults,
    EngineeringInputs,
    MaterialProperties,
    calculate_engineering_loads,
    format_fea_engineering_summary,
)
from cyclewash_fea_results import load_stage1_package
from cyclewash_fea_mapping import MappedFeaFields, map_fea_fields_to_stl
from cyclewash_geometry_policy import normalize_stl_part
from cyclewash_fea_runner import (
    FeaRunnerError,
    detect_fea_solver,
    package_matches_request,
    run_fea_subprocess,
    solver_request_hash,
)
from cyclewash_html_animation import (
    build_animation_payload,
    export_cyclewash_animation_html,
    renderer_asset_fingerprint,
)


DEFAULT_PARTS = [
    {
        "name": "Agitator",
        "filenames": ["Agitator.stl", "agitator.stl"],
    },
    {"name": "Inner Drum", "filenames": ["Inner Drum.stl", "Inner_Drum.stl", "inner_drum.stl"]},
    {"name": "dampener", "filenames": ["damper.stl", "dampener.stl"]},
    {"name": "dampener_001", "filenames": ["damper.001.stl", "damper_001.stl", "dampener.001.stl", "dampener_001.stl"]},
    {"name": "dampener_002", "filenames": ["damper.002.stl", "damper_002.stl", "dampener.002.stl", "dampener_002.stl"]},
    {"name": "dampener_003", "filenames": ["damper.003.stl", "damper_003.stl", "dampener.003.stl", "dampener_003.stl"]},
    {"name": "dampener_004", "filenames": ["damper.004.stl", "damper_004.stl", "dampener.004.stl", "dampener_004.stl"]},
    {"name": "dampener_005", "filenames": ["damper.005.stl", "damper_005.stl", "dampener.005.stl", "dampener_005.stl"]},
    {"name": "dampener_006", "filenames": ["damper.006.stl", "damper_006.stl", "dampener.006.stl", "dampener_006.stl"]},
    {"name": "dampener_007", "filenames": ["damper.007.stl", "damper_007.stl", "dampener.007.stl", "dampener_007.stl"]},
    {"name": "door", "filenames": ["door.stl"]},
    {"name": "enclosure", "filenames": ["enclosure.stl"]},
    {"name": "foot_001", "filenames": ["foot.001.stl", "foot_001.stl"]},
    {"name": "foot_002", "filenames": ["foot.002.stl", "foot_002.stl"]},
    {"name": "foot_003", "filenames": ["foot.003.stl", "foot_003.stl"]},
    {"name": "foot_004", "filenames": ["foot.004.stl", "foot_004.stl"]},
    {"name": "gear", "filenames": ["gear.stl"]},
    {"name": "shaft", "filenames": ["shaft.stl"]},
]


ROTATING_NAME_TOKENS = ("inner drum", "inner_drum", "drum", "agitator", "gear", "shaft")
PRIMARY_ROTATING_NAMES = {"inner drum", "inner_drum", "gear", "shaft"}
DOOR_NAME_TOKENS = ("door",)
DOOR_CLOSED_ROTATION_DEGREES = 90.0
FULL_OPACITY_NAME_TOKENS = ("inner drum", "inner_drum", "drum", "agitator", "gear", "shaft", "door")
COMPONENT_KIND_OPTIONS = ["casing", "shaft", "gear", "rotational"]
LOAD_CASE_SHORT_LABELS = {
    "Spin + water circulation through drum holes": "Spin + water",
    "Rotating drum / gear / shaft torsion": "Torsion",
    "Water load inside stationary drum": "Stationary water",
    "Door hinge closing load": "Door hinge",
}
ANALYSIS_SOURCES = ("Geometric preview", "Simplified Stage 1 FEA")
FEA_DISPLAY_MODES = ("STL-mapped results", "Solver mesh")
SELECTED_FEA_PACKAGE_SESSION_KEY = "selected_fea_package_path"
PROJECT_ROOT = CURRENT_DIR
FEA_RESULT_ROOT = CURRENT_DIR / "fea_results"
ANIMATION_EXPORT_SESSION_KEY = "cyclewash_html_animation_export"


@dataclass(frozen=True)
class AnimationExportSelection:
    """Effective animation mode and package for the current analysis source."""

    analysis_mode: str
    package: object | None
    caption: str


@dataclass(frozen=True)
class FeaActionState:
    """One honest action state for the current canonical FEA request."""

    mode: str
    action_label: str | None
    notice: str


def resolve_fea_action_state(
    *,
    cached_package_available: bool,
    solver_available: bool,
) -> FeaActionState:
    if cached_package_available:
        return FeaActionState(
            mode="cache",
            action_label="Load Cached Stage 1 FEA",
            notice="An exact solved Stage 1 FEA package matches these inputs.",
        )
    if solver_available:
        return FeaActionState(
            mode="solve",
            action_label="Run Stage 1 FEA",
            notice="The local Stage 1 FEA environment is available.",
        )
    return FeaActionState(
        mode="analytical",
        action_label=None,
        notice=(
            "Analytical preview updates for these inputs. Solved Stage 1 FEA for "
            "this combination must be run locally."
        ),
    )


REQUIRED_ANIMATION_PARTS = ("shaft", "gear", "inner drum")


@dataclass(frozen=True)
class FeaPhysicalMetrics:
    """Phase-specific physical values displayed beside the FEA mesh."""

    maximum_von_mises_pa: float
    maximum_displacement_m: float
    maximum_shear_pa: float | None
    minimum_factor_of_safety: float | None
    node_count: int
    element_count: int


@dataclass(frozen=True)
class FeaDisplaySelection:
    """Resolved Stage 1 geometry mode and its mapped-field evidence."""

    mode: str
    figure: go.Figure
    mapped_fields: MappedFeaFields | None
    source_path: Path | None
    geometry_description: str | None
    warning: str | None


def extract_boundary_faces(tetrahedra: np.ndarray) -> np.ndarray:
    """Return triangular faces belonging to exactly one tetrahedron."""

    tetrahedra = np.asarray(tetrahedra)
    if tetrahedra.ndim != 2 or tetrahedra.shape[1:] != (4,):
        raise ValueError("tetrahedra must have shape (element_count, 4)")
    if not np.issubdtype(tetrahedra.dtype, np.integer):
        raise ValueError("tetrahedra must contain integer node indices")
    if tetrahedra.size and np.min(tetrahedra) < 0:
        raise ValueError("tetrahedra must contain non-negative node indices")

    face_records: dict[tuple[int, int, int], tuple[int, tuple[int, int, int]]] = {}
    face_offsets = ((1, 2, 3), (0, 3, 2), (0, 1, 3), (0, 2, 1))
    for tetrahedron in tetrahedra:
        for offsets in face_offsets:
            face = tuple(int(tetrahedron[index]) for index in offsets)
            key = tuple(sorted(face))
            count, first_face = face_records.get(key, (0, face))
            face_records[key] = (count + 1, first_face)

    boundary = [face for count, face in face_records.values() if count == 1]
    if not boundary:
        return np.empty((0, 3), dtype=int)
    return np.asarray(boundary, dtype=int)


def select_fea_phase(
    phase_degrees: Sequence[float], requested_degrees: float
) -> tuple[int, float]:
    """Select the nearest available phase using circular degree distance."""

    phases = np.asarray(tuple(phase_degrees), dtype=float)
    if phases.ndim != 1 or phases.size == 0 or not np.isfinite(phases).all():
        raise ValueError("phase_degrees must contain finite values")
    if not math.isfinite(requested_degrees):
        raise ValueError("requested_degrees must be finite")
    distances = np.abs((phases - float(requested_degrees) + 180.0) % 360.0 - 180.0)
    index = int(np.argmin(distances))
    return index, float(phases[index])


def fea_physical_metrics(component, phase_index: int) -> FeaPhysicalMetrics:
    """Calculate physical metrics for one component phase without rescaling."""

    phase_count = component.von_mises_pa.shape[0]
    if not isinstance(phase_index, int) or isinstance(phase_index, bool):
        raise ValueError("phase_index must be an integer")
    if phase_index < 0 or phase_index >= phase_count:
        raise ValueError("phase_index is outside the component phase range")
    stress_pa = np.asarray(component.von_mises_pa[phase_index], dtype=float)
    displacement_m = np.asarray(component.displacement_m[phase_index], dtype=float)
    displacement_magnitude = np.linalg.norm(displacement_m, axis=1)
    factor_of_safety = getattr(component, "nodal_factor_of_safety", None)
    maximum_shear = getattr(component, "nodal_maximum_shear_pa", None)
    minimum_factor = (
        None
        if factor_of_safety is None
        else float(np.min(np.asarray(factor_of_safety[phase_index], dtype=float)))
    )
    return FeaPhysicalMetrics(
        maximum_von_mises_pa=float(np.max(stress_pa)),
        maximum_displacement_m=float(np.max(displacement_magnitude)),
        maximum_shear_pa=(
            None
            if maximum_shear is None
            else float(np.max(np.asarray(maximum_shear[phase_index], dtype=float)))
        ),
        minimum_factor_of_safety=minimum_factor,
        node_count=int(component.vertices_m.shape[0]),
        element_count=int(component.tetrahedra.shape[0]),
    )


def fea_package_path(
    project_root: Path,
    inputs: EngineeringInputs,
    mesh_levels: Sequence[str],
) -> Path:
    """Return the deterministic published package path for one request."""

    return (
        Path(project_root)
        / "fea_results"
        / solver_request_hash(inputs, mesh_levels)
    )


def package_path_for_source(
    analysis_source: str,
    selected_path: Path | str | None,
    expected_path: Path,
) -> Path | None:
    """Expose only the current FEA request, never geometric or stale results."""

    if analysis_source != "Simplified Stage 1 FEA" or selected_path is None:
        return None
    selected = Path(selected_path)
    expected = Path(expected_path)
    return selected if selected == expected else None


def updated_selected_package_path(
    previous_path: Path | str | None,
    candidate_path: Path,
    *,
    solve_succeeded: bool,
) -> Path | None:
    """Update package selection only after a successful matching solve."""

    return Path(candidate_path) if solve_succeeded else (
        None if previous_path is None else Path(previous_path)
    )


def require_matching_package(package, inputs: EngineeringInputs, mesh_levels: Sequence[str]):
    """Reject schema-valid packages whose canonical request content differs."""

    if not package_matches_request(package, inputs, mesh_levels):
        raise ValueError("FEA package content does not match the current FEA request")
    return package


class StreamlitFeaProgress:
    """Adapt runner progress events to Streamlit's progress and status widgets."""

    def __init__(self, progress_widget, status_widget):
        self.progress_widget = progress_widget
        self.status_widget = status_widget

    def __call__(self, fraction: float, message: str) -> None:
        self.progress_widget.progress(
            fraction, text=f"{fraction * 100.0:.0f}% - {message}"
        )
        self.status_widget.update(label=message, state="running")

    def complete(self) -> None:
        self.progress_widget.progress(1.0, text="100% - Stage 1 FEA complete")
        self.status_widget.update(label="Stage 1 FEA complete", state="complete")
        self.progress_widget.empty()

    def fail(self, message: str) -> None:
        self.progress_widget.empty()
        self.status_widget.update(label=message, state="error")


def animation_export_output_path(project_root: Path) -> Path:
    """Return the fixed shared-machine HTML animation destination."""

    return Path(project_root) / "cyclewash_fea_animation.html"


def _update_hash_with_array(hasher, array: np.ndarray) -> None:
    contiguous = np.ascontiguousarray(array)
    hasher.update(str(contiguous.dtype).encode("ascii"))
    hasher.update(json.dumps(contiguous.shape).encode("ascii"))
    hasher.update(contiguous.tobytes(order="C"))


def animation_export_fingerprint(
    *,
    parts: Sequence[AssemblyPart],
    source_fingerprints: Sequence[str],
    inputs: EngineeringInputs,
    analysis_mode: str,
    package_fingerprint: str | None,
    colorscale: str,
    rotation_axis: Sequence[float],
    renderer_fingerprint: str,
) -> str:
    """Hash every app-side dependency that can select or alter an export."""

    part_list = tuple(parts)
    sources = tuple(source_fingerprints)
    if len(part_list) != len(sources):
        raise ValueError("source_fingerprints must contain one value per part")
    if analysis_mode not in {"geometric", "fea"}:
        raise ValueError("analysis_mode must be geometric or fea")
    effective_axis = _normalized_animation_rotation_axis(rotation_axis)
    hasher = hashlib.sha256()
    hasher.update(b"cyclewash-streamlit-animation-export-v2\0")
    settings = {
        "analysis_mode": analysis_mode,
        "colorscale": colorscale,
        "engineering_inputs": inputs.to_dict(),
        "package_fingerprint": package_fingerprint,
        "renderer_fingerprint": renderer_fingerprint,
        "rotation_axis": effective_axis,
    }
    hasher.update(
        json.dumps(
            settings, allow_nan=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    )
    for part, source_fingerprint in zip(part_list, sources):
        hasher.update(
            json.dumps(
                {
                    "component_kind": part.component_kind,
                    "material_color": part.material_color,
                    "name": part.name,
                    "source_fingerprint": source_fingerprint,
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        _update_hash_with_array(hasher, np.asarray(part.vertices))
        _update_hash_with_array(hasher, np.asarray(part.faces))
    return hasher.hexdigest()


def animation_export_state(
    fingerprint: str,
    output_path: Path,
    analysis_mode: str,
    registration_warnings: Sequence[str],
) -> dict[str, object]:
    """Build session metadata for one completed HTML export."""

    path = Path(output_path).resolve()
    content = path.read_bytes()
    return {
        "analysis_mode": analysis_mode,
        "fingerprint": fingerprint,
        "html_sha256": hashlib.sha256(content).hexdigest(),
        "path": str(path),
        "registration_warnings": tuple(str(item) for item in registration_warnings),
        "size_bytes": len(content),
    }


def can_reuse_animation_export(
    state: Mapping[str, object] | None,
    fingerprint: str,
    output_path: Path,
) -> bool:
    """Return whether session metadata still describes the exact HTML file."""

    if not isinstance(state, Mapping) or state.get("fingerprint") != fingerprint:
        return False
    path = Path(output_path).resolve()
    if state.get("path") != str(path) or not path.is_file():
        return False
    content = path.read_bytes()
    return (
        state.get("size_bytes") == len(content)
        and state.get("html_sha256") == hashlib.sha256(content).hexdigest()
    )


def updated_animation_export_state(
    previous_state,
    candidate_state,
    *,
    export_succeeded: bool,
):
    """Preserve prior session state unless a complete export succeeded."""

    return candidate_state if export_succeeded else previous_state


def _normalized_animation_rotation_axis(
    rotation_axis: Sequence[float],
) -> tuple[float, float, float]:
    """Return the effective unit rotation axis used by the HTML renderer."""

    try:
        axis = np.asarray(rotation_axis, dtype=float)
    except (TypeError, ValueError) as error:
        raise ValueError("rotation_axis must be a finite nonzero 3-vector") from error
    if axis.shape != (3,) or not np.isfinite(axis).all():
        raise ValueError("rotation_axis must be a finite nonzero 3-vector")
    magnitude = float(np.linalg.norm(axis))
    if magnitude <= 0.0:
        raise ValueError("rotation_axis must be a finite nonzero 3-vector")
    return tuple(float(value) for value in axis / magnitude)


def resolve_animation_export_mode(
    analysis_source: str,
    package,
    inputs: EngineeringInputs,
    mesh_levels: Sequence[str],
) -> AnimationExportSelection:
    """Resolve FEA-backed export or an explicit geometric-preview fallback."""

    if analysis_source == "Geometric preview":
        return AnimationExportSelection(
            "geometric", None, "Export mode: Geometric preview."
        )
    if analysis_source != "Simplified Stage 1 FEA":
        raise ValueError(f"Unsupported analysis source: {analysis_source}")
    if package is not None and package_matches_request(package, inputs, mesh_levels):
        return AnimationExportSelection(
            "fea",
            package,
            "Export mode: FEA-backed animation from the matching loaded package.",
        )
    reason = (
        "no matching Stage 1 FEA package is loaded"
        if package is None
        else "the loaded Stage 1 FEA package does not match the current request"
    )
    return AnimationExportSelection(
        "geometric",
        None,
        f"Export mode: Geometric preview fallback because {reason}.",
    )


def build_animation_export_payload(
    *,
    parts: Sequence[AssemblyPart],
    inputs: EngineeringInputs,
    analytical,
    selection: AnimationExportSelection,
    colorscale: str,
    rotation_axis: Sequence[float],
):
    """Build a payload using the exact mode and display options selected for export."""

    physical_parts = [normalize_stl_part(part).part for part in parts]
    return build_animation_payload(
        physical_parts,
        inputs,
        analytical,
        selection.package,
        colorscale=colorscale,
        rotation_axis=_normalized_animation_rotation_axis(rotation_axis),
    )


def animation_package_content_fingerprint(package_path: Path, package) -> str:
    """Hash package request identity, schema, and every published package file."""

    directory = Path(package_path)
    hasher = hashlib.sha256()
    hasher.update(str(package.schema_version).encode("utf-8"))
    hasher.update(
        json.dumps(
            package.assumptions.get("request_identity"),
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    for path in sorted(item for item in directory.iterdir() if item.is_file()):
        stat = path.stat()
        hasher.update(path.name.encode("utf-8"))
        hasher.update(f"{stat.st_size}:{stat.st_mtime_ns}".encode("ascii"))
        hasher.update(hashlib.sha256(path.read_bytes()).digest())
    return hasher.hexdigest()


def _source_fingerprint(source) -> str:
    """Hash one STL source without substituting generated demonstration geometry."""

    hasher = hashlib.sha256()
    if isinstance(source, (str, Path)):
        path = Path(source).resolve()
        hasher.update(str(path).encode("utf-8"))
        hasher.update(path.read_bytes())
    elif isinstance(source, bytes):
        hasher.update(source)
    else:
        position = source.tell() if hasattr(source, "tell") else None
        data = source.read()
        if position is not None and hasattr(source, "seek"):
            source.seek(position)
        hasher.update(data.encode("utf-8") if isinstance(data, str) else data)
    return hasher.hexdigest()


def animation_source_fingerprints(
    parts: Sequence[AssemblyPart], specs: Sequence[StlPartSpec]
) -> tuple[str, ...]:
    """Return source fingerprints aligned with successfully loaded parts."""

    available: dict[str, list[StlPartSpec]] = {}
    for spec in specs:
        available.setdefault(spec.name, []).append(spec)
    fingerprints = []
    for part in parts:
        candidates = available.get(part.name, [])
        if not candidates:
            raise ValueError(f"No STL source fingerprint is available for {part.name}")
        fingerprints.append(_source_fingerprint(candidates.pop(0).source))
    return tuple(fingerprints)


def missing_animation_parts(parts: Sequence[AssemblyPart]) -> tuple[str, ...]:
    """Return required rotating assembly names absent from the loaded STL parts."""

    normalized = {_normalize_part_name(part.name) for part in parts}
    return tuple(
        required
        for required in REQUIRED_ANIMATION_PARTS
        if not any(required in name for name in normalized)
    )


def animation_geometry_ready(parts: Sequence[AssemblyPart]) -> bool:
    return bool(parts) and not missing_animation_parts(parts)


def _normalize_part_name(name: str) -> str:
    """Normalize Blender/file part names for matching and defaults."""
    return name.lower().replace("-", " ").replace("_", " ").replace(".", " ").strip()


def format_axis_label(axis: tuple[float, float, float]) -> str:
    """Return a compact label for a basis or custom 3D axis."""
    basis_labels = {
        (1.0, 0.0, 0.0): "+X",
        (-1.0, 0.0, 0.0): "-X",
        (0.0, 1.0, 0.0): "+Y",
        (0.0, -1.0, 0.0): "-Y",
        (0.0, 0.0, 1.0): "+Z",
        (0.0, 0.0, -1.0): "-Z",
    }
    for basis, label in basis_labels.items():
        if np.allclose(axis, basis):
            return label
    return f"({axis[0]:g}, {axis[1]:g}, {axis[2]:g})"


def _infer_component_kind(name: str) -> str:
    """Infer the structural visualization type from a Blender object or STL name."""
    normalized_name = _normalize_part_name(name)
    if "shaft" in normalized_name:
        return "shaft"
    if "gear" in normalized_name:
        return "gear"
    if any(token.replace("_", " ") in normalized_name for token in ROTATING_NAME_TOKENS):
        return "rotational"
    return "casing"


def _infer_material_color(name: str, component_kind: str) -> str:
    """Choose a stable material color for a named assembly component."""
    normalized_name = _normalize_part_name(name)
    if "shaft" in normalized_name:
        return "#d97706"
    if "gear" in normalized_name:
        return "#f59e0b"
    if "inner drum" in normalized_name or "drum" in normalized_name:
        return "#60a5fa"
    if "agitator" in normalized_name:
        return "#38bdf8"
    if "door" in normalized_name:
        return "#64748b"
    if "damper" in normalized_name or "dampener" in normalized_name:
        return "#a855f7"
    if "foot" in normalized_name:
        return "#475569"
    if component_kind == "casing":
        return "#94a3b8"
    return "#9ca3af"


def _is_primary_rotating_part(name: str) -> bool:
    """Return whether a part is one of the requested analysis priorities."""
    normalized_name = _normalize_part_name(name)
    return any(token.replace("_", " ") in normalized_name for token in PRIMARY_ROTATING_NAMES)


def _is_rotating_assembly_part(name: str) -> bool:
    """Return whether a part should rotate with the drum/shaft/gear group."""
    normalized_name = _normalize_part_name(name)
    return any(token.replace("_", " ") in normalized_name for token in ROTATING_NAME_TOKENS)


def _is_door_part(name: str) -> bool:
    """Return whether a part should use the door hinge pose."""
    normalized_name = _normalize_part_name(name)
    return any(token in normalized_name for token in DOOR_NAME_TOKENS)


def _make_part_spec(name: str, source) -> StlPartSpec:
    """Create a zero-offset part spec using inferred metadata."""
    component_kind = _infer_component_kind(name)
    return StlPartSpec(
        name=name,
        source=source,
        translation=(0.0, 0.0, 0.0),
        material_color=_infer_material_color(name, component_kind),
        component_kind=component_kind,
    )


def _estimate_door_hinge_origin(part: AssemblyPart, hinge_side: str) -> tuple[float, float, float]:
    """Estimate a hinge origin from vertices on the selected door boundary."""
    vertices = part.vertices
    bounds_min = vertices.min(axis=0)
    bounds_max = vertices.max(axis=0)
    center = (bounds_min + bounds_max) / 2.0
    side_map = {
        "min x": (0, bounds_min[0]),
        "max x": (0, bounds_max[0]),
        "min y": (1, bounds_min[1]),
        "max y": (1, bounds_max[1]),
    }
    selection = side_map.get(hinge_side.lower())
    if selection is None:
        origin = center
    else:
        axis_index, extreme = selection
        axis_span = float(bounds_max[axis_index] - bounds_min[axis_index])
        tolerance = max(axis_span * 1.0e-6, 1.0e-9)
        boundary_mask = np.isclose(
            vertices[:, axis_index],
            extreme,
            atol=tolerance,
            rtol=0.0,
        )
        origin = vertices[boundary_mask].mean(axis=0)
        origin[axis_index] = extreme
    return (float(origin[0]), float(origin[1]), float(origin[2]))


def closed_door_pose_angle(opening_degrees: float) -> float:
    """Map a door opening angle onto the exported STL's open-pose rotation."""
    opening = float(opening_degrees)
    if not math.isfinite(opening) or not 0.0 <= opening <= 90.0:
        raise ValueError("door opening angle must be between 0 and 90 degrees")
    return DOOR_CLOSED_ROTATION_DEGREES - opening


def infer_rotating_axis_origin(parts: list[AssemblyPart]) -> tuple[float, float, float]:
    """Infer one shared rotating-axis point from shaft or rotating-group geometry."""
    shaft_parts = [part for part in parts if "shaft" in _normalize_part_name(part.name)]
    if shaft_parts:
        vertices = np.vstack([part.vertices for part in shaft_parts])
        bounds_center = (vertices.min(axis=0) + vertices.max(axis=0)) / 2.0
        return tuple(float(value) for value in bounds_center)

    source_parts = [part for part in parts if _is_rotating_assembly_part(part.name)]
    if not source_parts:
        return (0.0, 0.0, 0.0)

    vertices = np.vstack([part.vertices for part in source_parts])
    centroid = vertices.mean(axis=0)
    return tuple(float(value) for value in centroid)


def _apply_motion_pose(
    part: AssemblyPart,
    rotation_axis: tuple[float, float, float],
    rotation_origin: tuple[float, float, float],
    rotation_angle_degrees: float,
    door_axis: tuple[float, float, float],
    door_angle_degrees: float,
    door_hinge_origin: tuple[float, float, float],
) -> AssemblyPart:
    """Apply rotating assembly and door hinge pose transforms."""
    world_vertices = part.vertices
    world_part = AssemblyPart(
        name=part.name,
        local_vertices=world_vertices,
        faces=part.faces,
        material_color=part.material_color,
        component_kind=part.component_kind,
    )
    if _is_rotating_assembly_part(part.name):
        return world_part.transformed(
            Transform.from_rotation(
                rotation_axis,
                rotation_angle_degrees,
                origin=rotation_origin,
            )
        )
    if _is_door_part(part.name):
        return world_part.transformed(
            Transform.from_rotation(
                door_axis,
                door_angle_degrees,
                origin=door_hinge_origin,
            )
        )
    return part


def _load_available_default_specs(stl_directory: Path) -> list[StlPartSpec]:
    """Load part specs for default STL paths that exist on disk."""
    specs: list[StlPartSpec] = []
    matched_paths: set[Path] = set()
    if not stl_directory.exists():
        return specs

    stl_paths = sorted(path for path in stl_directory.glob("*.stl") if path.is_file())
    paths_by_lower_name = {path.name.lower(): path for path in stl_paths}

    for part in DEFAULT_PARTS:
        for filename in part["filenames"]:
            path = paths_by_lower_name.get(filename.lower())
            if path is not None and path not in matched_paths:
                specs.append(_make_part_spec(part["name"], path))
                matched_paths.add(path)
                break

    for path in stl_paths:
        if path not in matched_paths:
            specs.append(_make_part_spec(path.stem, path))
    return specs


def _load_specs_from_path(stl_path: Path) -> list[StlPartSpec]:
    """Load one STL file or scan a folder for STL assembly components."""
    if stl_path.is_file() and stl_path.suffix.lower() == ".stl":
        return [_make_part_spec(stl_path.stem, stl_path)]
    if stl_path.is_dir():
        return _load_available_default_specs(stl_path)
    return []


@dataclass(frozen=True)
class PartPresentation:
    """Automatic display settings for one component in a load case."""

    visible: bool
    heatmap: bool
    opacity: float


def load_case_presentation(
    parts: list[AssemblyPart],
    load_case: str,
) -> dict[str, PartPresentation]:
    """Return deterministic visibility and stress-color settings for a load case."""
    heatmap_tokens = {
        "Spin + water circulation through drum holes": ("inner drum", "gear"),
        "Rotating drum / gear / shaft torsion": ("inner drum", "gear", "shaft"),
        "Water load inside stationary drum": ("inner drum",),
        "Door hinge closing load": ("door",),
    }
    context_tokens = {
        "Spin + water circulation through drum holes": ("shaft", "door", "enclosure"),
        "Rotating drum / gear / shaft torsion": ("door", "enclosure"),
        "Water load inside stationary drum": ("door", "enclosure"),
        "Door hinge closing load": ("enclosure",),
    }
    if load_case not in heatmap_tokens:
        raise ValueError(f"Unsupported load case: {load_case}")

    active_heatmap = heatmap_tokens[load_case]
    active_context = context_tokens[load_case]
    presentation: dict[str, PartPresentation] = {}
    for part in parts:
        normalized_name = _normalize_part_name(part.name)
        heatmap = any(token in normalized_name for token in active_heatmap)
        context = any(token in normalized_name for token in active_context)
        if "enclosure" in normalized_name:
            opacity = 0.5
        elif context and "door" in normalized_name:
            opacity = 0.5
        else:
            opacity = 1.0
        presentation[part.name] = PartPresentation(
            visible=heatmap or context,
            heatmap=heatmap,
            opacity=opacity,
        )
    return presentation


def build_structural_figure(
    parts: list[AssemblyPart],
    presentation: dict[str, PartPresentation],
    load_case: str,
    colorscale: str,
    casing_axis: tuple[float, float, float],
    rotation_axis: tuple[float, float, float],
    rotation_origin: tuple[float, float, float],
    rotation_angle_degrees: float = 0.0,
    drum_speed_rpm: float = 60.0,
    water_fill_fraction: float = 0.35,
    perforation_relief: float = 0.45,
    door_axis: tuple[float, float, float] = (0.0, 0.0, -1.0),
    door_hinge_origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> go.Figure:
    """Build a Plotly figure for the current assembly stress view."""
    figure = go.Figure()
    stress_colorbar_shown = False
    for part in parts:
        style = presentation[part.name]
        if not style.visible:
            continue

        stress_values = None
        if style.heatmap and load_case == "Spin + water circulation through drum holes":
            stress_values = rotating_water_stress(
                part,
                rotation_axis=rotation_axis,
                gravity_axis=casing_axis,
                axis_origin=rotation_origin,
                rpm=drum_speed_rpm,
                rotation_angle_degrees=rotation_angle_degrees,
                water_fill_fraction=water_fill_fraction,
                perforation_relief=perforation_relief,
            )
        elif style.heatmap and load_case == "Rotating drum / gear / shaft torsion":
            stress_values = radial_torsion_stress(
                part,
                axis_vector=rotation_axis,
                axis_origin=rotation_origin,
            )
        elif style.heatmap and load_case == "Water load inside stationary drum":
            stress_values = stationary_water_stress(
                part,
                gravity_axis=casing_axis,
                water_fill_fraction=water_fill_fraction,
                perforation_relief=perforation_relief,
            )
        elif style.heatmap and load_case == "Door hinge closing load":
            stress_values = hinge_lever_stress(
                part,
                hinge_axis=door_axis,
                hinge_origin=door_hinge_origin,
            )

        figure.add_trace(
            go.Mesh3d(
                **mesh3d_kwargs(
                    part,
                    stress_values=stress_values,
                    color_mode="Stress Visualization" if style.heatmap else "Material Color",
                    colorscale=colorscale,
                    opacity=style.opacity,
                    show_scale=stress_values is not None and not stress_colorbar_shown,
                )
            )
        )
        if stress_values is not None:
            stress_colorbar_shown = True

    figure.update_layout(
        height=720,
        margin={"l": 0, "r": 0, "t": 12, "b": 0},
        scene={
            "aspectmode": "data",
            "xaxis": {"title": "X"},
            "yaxis": {"title": "Y"},
            "zaxis": {"title": "Z"},
            "camera": {"eye": {"x": 1.4, "y": 1.5, "z": 1.1}},
        },
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.01, "xanchor": "left", "x": 0},
    )
    return figure


def build_stage1_analytical_preview(
    parts: Sequence[AssemblyPart],
    inputs: EngineeringInputs,
    analytical: AnalyticalResults,
    colorscale: str,
    phase_degrees: float,
) -> tuple[go.Figure, str]:
    """Build a geometric STL load map for a valid unsolved input request."""

    part_list = list(parts)
    if not part_list:
        raise ValueError("analytical preview requires loaded STL parts")
    rotation_axis = (1.0, 0.0, 0.0)
    rotation_origin = infer_rotating_axis_origin(part_list)
    rotation_angle = float(phase_degrees) % 360.0
    display_parts = [
        _apply_motion_pose(
            part,
            rotation_axis=rotation_axis,
            rotation_origin=rotation_origin,
            rotation_angle_degrees=rotation_angle,
            door_axis=(0.0, 0.0, -1.0),
            door_angle_degrees=0.0,
            door_hinge_origin=(0.0, 0.0, 0.0),
        )
        for part in part_list
    ]
    load_case = "Spin + water circulation through drum holes"
    presentation = load_case_presentation(display_parts, load_case)
    figure = build_structural_figure(
        parts=display_parts,
        presentation=presentation,
        load_case=load_case,
        colorscale=colorscale,
        casing_axis=(0.0, 0.0, -1.0),
        rotation_axis=rotation_axis,
        rotation_origin=rotation_origin,
        rotation_angle_degrees=rotation_angle,
        drum_speed_rpm=inputs.speed_rpm,
        water_fill_fraction=inputs.fill_fraction,
        perforation_relief=inputs.perforation_relief,
        door_axis=(0.0, 0.0, -1.0),
        door_hinge_origin=(0.0, 0.0, 0.0),
    )
    summary = format_fea_engineering_summary(
        inputs,
        analytical,
        package_summary=(
            "Analytical preview from the current STL geometry and reduced-order "
            "loads. No solved FEA package is attached."
        ),
    )
    return figure, summary


def format_structural_math_summary(
    load_case: str,
    principal_parts: tuple[str, ...],
    drum_speed_rpm: float,
    simulation_time_s: float,
    phase_offset_degrees: float,
    rotation_angle_degrees: float,
    water_fill_fraction: float,
    perforation_relief: float,
    casing_axis: tuple[float, float, float],
    rotation_axis: tuple[float, float, float],
    rotation_origin: tuple[float, float, float],
    door_axis: tuple[float, float, float],
    door_hinge_origin: tuple[float, float, float],
) -> str:
    """Return a copyable, load-case-specific explanation of the stress model."""
    omega_rad_s = 2.0 * math.pi * drum_speed_rpm / 60.0
    theta_rad = math.radians(rotation_angle_degrees)
    retained_water = water_fill_fraction * (1.0 - perforation_relief)
    rpm_scale = min((drum_speed_rpm / 120.0) ** 2, 4.0)
    principal_text = ", ".join(principal_parts) if principal_parts else "none found"
    header = f"""CycleWash Stress Model
Normalized geometric approximation (0.0 to 1.0); this is not FEA.

Load case: {load_case}
Heatmapped parts: {principal_text}
"""

    spin_text = f"""
Angular kinematics
  N = {drum_speed_rpm:.3f} rev/min
  ω = 2πN/60 = {omega_rad_s:.4f} rad/s
  θ(t) = θ₀ + ωt = {phase_offset_degrees:.3f}° + ω({simulation_time_s:.3f} s)
       = {rotation_angle_degrees:.3f}° = {theta_rad:.4f} rad

Shared shaft-axis transform
  a = unit(rotation axis) = {rotation_axis}
  o = inferred shaft-axis origin = {rotation_origin}
  x_world(θ) = Rₐ(θ)·(x_local - o) + o
  r⊥ = r - (r·a)a
  v = ω × r⊥,  |v| = ω|r⊥|
"""

    if load_case == "Spin + water circulation through drum holes":
        body = spin_text + f"""
Spin and water-circulation model
  g = unit(gravity/load axis) = {casing_axis}
  water retained = fill·(1 - relief)
                 = {water_fill_fraction:.3f}·(1 - {perforation_relief:.3f})
                 = {retained_water:.3f}
  rpm scale = min((N/120)², 4) = {rpm_scale:.3f}
  radial = normalize(|r⊥|²)
  head = normalize(x·g)
  circulation = 0.5 + 0.5·sin(φ - θ)
  σ̂ = normalize(0.55·rpm_scale·radial
                 + water_retained·(0.30·head + 0.15·circulation))

Interpretation
  σ̂ combines radial spin loading, retained-water head, and cyclic flow
  through the drum perforations. Red indicates the highest relative load.
"""
    elif load_case == "Rotating drum / gear / shaft torsion":
        body = spin_text + """
Torsional shear model
  τ̂ = normalize(|r⊥|)

Interpretation
  Relative torsional shear increases with perpendicular distance from the
  shaft axis. This is a geometric trend, not a material stress in pascals.
"""
    elif load_case == "Water load inside stationary drum":
        body = f"""
Stationary hydrostatic model
  g = unit(gravity/load axis) = {casing_axis}
  u = normalize(x·g)
  free surface = 1 - fill = {1.0 - water_fill_fraction:.3f}
  d̂ = clip(u - (1 - fill), 0, fill)
  σ̂ = (1 - relief)·d̂
  max(σ̂) = fill·(1 - relief) = {retained_water:.3f}

Interpretation
  The normalized load rises with hydrostatic head toward the bottom of the
  drum. Perforation relief reduces the retained-water contribution.
"""
    elif load_case == "Door hinge closing load":
        body = f"""
Door hinge-lever model
  h = unit(door axis) = {door_axis}
  p = hinge origin = {door_hinge_origin}
  d_hinge = |(x - p) - ((x - p)·h)h|
  σ̂ = normalize(d_hinge)

Interpretation
  Relative hinge loading increases with perpendicular distance from the
  hinge line. The displayed opening pose does not change this normalized
  lever-arm relationship.
"""
    else:
        raise ValueError(f"Unsupported load case: {load_case}")

    return header + body


def build_fea_figure(
    component,
    phase_index: int,
    component_name: str,
    colorscale: str = "Turbo",
) -> go.Figure:
    """Build an undeformed-scale Plotly surface from physical tetrahedral fields."""

    faces = extract_boundary_faces(component.tetrahedra)
    if faces.size == 0:
        raise ValueError(f"{component_name} mesh has no exterior tetrahedral faces")

    stress_pa = np.asarray(component.von_mises_pa[phase_index], dtype=float)
    displacement_m = np.asarray(component.displacement_m[phase_index], dtype=float)
    displaced_vertices_mm = (
        np.asarray(component.vertices_m, dtype=float) + displacement_m
    ) * 1000.0
    displacement_mm = np.linalg.norm(displacement_m, axis=1) * 1000.0
    stress_mpa = stress_pa / 1.0e6
    customdata = np.column_stack((stress_mpa, displacement_mm))

    stress_min = float(np.min(stress_pa))
    stress_max = float(np.max(stress_pa))
    if math.isclose(stress_min, stress_max):
        tick_values = [stress_min]
    else:
        tick_values = np.linspace(stress_min, stress_max, 5).tolist()
    tick_text = [f"{value / 1.0e6:.3g}" for value in tick_values]

    figure = go.Figure(
        go.Mesh3d(
            x=displaced_vertices_mm[:, 0],
            y=displaced_vertices_mm[:, 1],
            z=displaced_vertices_mm[:, 2],
            i=faces[:, 0],
            j=faces[:, 1],
            k=faces[:, 2],
            intensity=stress_pa,
            intensitymode="vertex",
            colorscale=colorscale,
            colorbar={
                "title": {"text": "von Mises<br>(MPa)"},
                "tickvals": tick_values,
                "ticktext": tick_text,
            },
            customdata=customdata,
            hovertemplate=(
                "von Mises: %{customdata[0]:.3f} MPa<br>"
                "Displacement: %{customdata[1]:.4f} mm<extra></extra>"
            ),
            name=component_name.title(),
            showscale=True,
        )
    )
    figure.update_layout(
        height=680,
        margin={"l": 0, "r": 0, "t": 36, "b": 0},
        title=f"{component_name.title()} parametric FEA mesh",
        scene={
            "aspectmode": "data",
            "xaxis": {"title": "X (mm)"},
            "yaxis": {"title": "Y (mm)"},
            "zaxis": {"title": "Z (mm)"},
            "camera": {"eye": {"x": 1.4, "y": 1.5, "z": 1.1}},
        },
    )
    return figure


def _physical_context_part(part: AssemblyPart) -> AssemblyPart:
    """Normalize assembly context and return the door in its closed pose."""

    physical = normalize_stl_part(part).part
    if not _is_door_part(physical.name):
        return physical
    hinge_origin = _estimate_door_hinge_origin(physical, "min X")
    return _apply_motion_pose(
        physical,
        rotation_axis=(1.0, 0.0, 0.0),
        rotation_origin=(0.0, 0.0, 0.0),
        rotation_angle_degrees=0.0,
        door_axis=(0.0, 0.0, -1.0),
        door_angle_degrees=closed_door_pose_angle(0.0),
        door_hinge_origin=hinge_origin,
    )


def build_mapped_fea_figure(
    mapped: MappedFeaFields,
    phase_index: int,
    component_name: str,
    context_parts: Sequence[AssemblyPart] = (),
    colorscale: str = "Turbo",
) -> go.Figure:
    """Render physical Stage 1 fields on an undeformed authoritative STL."""

    if phase_index < 0 or phase_index >= len(mapped.phase_degrees):
        raise ValueError("phase_index is outside the mapped component phase range")
    vertices_mm = np.asarray(mapped.vertices_m, dtype=float) * 1000.0
    faces = np.asarray(mapped.faces, dtype=np.int64)
    stress_pa = np.asarray(mapped.stress_pa[phase_index], dtype=float)
    displacement_mm = np.linalg.norm(
        np.asarray(mapped.displacement_m[phase_index], dtype=float), axis=1
    ) * 1000.0
    stress_mpa = stress_pa / 1.0e6
    customdata = np.column_stack((stress_mpa, displacement_mm))
    stress_min = float(np.min(stress_pa))
    stress_max = float(np.max(stress_pa))
    tick_values = (
        [stress_min]
        if math.isclose(stress_min, stress_max)
        else np.linspace(stress_min, stress_max, 5).tolist()
    )

    figure = go.Figure()
    figure.add_trace(
        go.Mesh3d(
            x=vertices_mm[:, 0],
            y=vertices_mm[:, 1],
            z=vertices_mm[:, 2],
            i=faces[:, 0],
            j=faces[:, 1],
            k=faces[:, 2],
            intensity=stress_pa,
            intensitymode="vertex",
            colorscale=colorscale,
            colorbar={
                "title": {"text": "von Mises<br>(MPa)"},
                "tickvals": tick_values,
                "ticktext": [f"{value / 1.0e6:.3g}" for value in tick_values],
            },
            customdata=customdata,
            hovertemplate=(
                "von Mises: %{customdata[0]:.3f} MPa<br>"
                "Displacement: %{customdata[1]:.4f} mm<extra></extra>"
            ),
            name=component_name.title(),
            showscale=True,
        )
    )
    for raw_part in context_parts:
        normalized_name = _normalize_part_name(raw_part.name)
        if "enclosure" not in normalized_name and "door" not in normalized_name:
            continue
        context = _physical_context_part(raw_part)
        context_vertices_mm = np.asarray(context.vertices, dtype=float) * 1000.0
        figure.add_trace(
            go.Mesh3d(
                x=context_vertices_mm[:, 0],
                y=context_vertices_mm[:, 1],
                z=context_vertices_mm[:, 2],
                i=context.faces[:, 0],
                j=context.faces[:, 1],
                k=context.faces[:, 2],
                color="#9ca3af",
                opacity=0.5,
                flatshading=False,
                hoverinfo="name",
                name=raw_part.name,
                showscale=False,
            )
        )
    figure.update_layout(
        height=680,
        margin={"l": 0, "r": 0, "t": 36, "b": 0},
        title=f"{component_name.title()} physical FEA fields on authoritative STL",
        paper_bgcolor="#0e1117",
        plot_bgcolor="#0e1117",
        font={"color": "#e5e7eb"},
        scene={
            "aspectmode": "data",
            "bgcolor": "#0e1117",
            "xaxis": {"title": "X (mm)", "gridcolor": "#263244"},
            "yaxis": {"title": "Y (mm)", "gridcolor": "#263244"},
            "zaxis": {"title": "Z (mm)", "gridcolor": "#263244"},
            "camera": {"eye": {"x": 1.4, "y": 1.5, "z": 1.1}},
        },
    )
    return figure


def _part_matches_fea_component(part_name: str, component_name: str) -> bool:
    normalized = _normalize_part_name(part_name)
    if component_name == "drum":
        return "inner drum" in normalized or normalized == "drum"
    return component_name in normalized


def _source_path_for_part(
    part: AssemblyPart, source_paths: Mapping[str, Path]
) -> Path | None:
    target = _normalize_part_name(part.name)
    for name, path in source_paths.items():
        if _normalize_part_name(name) == target:
            return Path(path)
    return None


def mapping_warning_message(mapped: MappedFeaFields) -> str | None:
    """Describe registration and projection warnings without conflating them."""

    reasons: list[str] = []
    registration = mapped.metadata["registration"]
    if registration.get("warning"):
        reasons.append(str(registration["warning_message"]))
    if mapped.metadata.get("projection_warning"):
        p95_mm = mapped.metadata["projection_error_m"]["p95"] * 1000.0
        tolerance_mm = mapped.metadata["projection_warning_tolerance_m"] * 1000.0
        reasons.append(
            f"Field projection P95 is {p95_mm:.3f} mm, above the "
            f"{tolerance_mm:.3f} mm tolerance."
        )
    return " ".join(reasons) or None


def resolve_fea_display(
    *,
    component,
    phase_index: int,
    component_name: str,
    requested_mode: str,
    colorscale: str,
    assembly_parts: Sequence[AssemblyPart],
    source_paths: Mapping[str, Path],
    load_errors: Mapping[str, str] | None = None,
) -> FeaDisplaySelection:
    """Resolve mapped STL display or a component-specific solver fallback."""

    if requested_mode not in FEA_DISPLAY_MODES:
        raise ValueError(f"Unsupported FEA display mode: {requested_mode}")
    if requested_mode == "Solver mesh":
        return FeaDisplaySelection(
            mode="Solver mesh",
            figure=build_fea_figure(component, phase_index, component_name, colorscale),
            mapped_fields=None,
            source_path=None,
            geometry_description=None,
            warning=None,
        )

    display_part = next(
        (
            part
            for part in assembly_parts
            if _part_matches_fea_component(part.name, component_name)
        ),
        None,
    )
    if display_part is None:
        matching_name = next(
            (
                name
                for name in source_paths
                if _part_matches_fea_component(name, component_name)
            ),
            None,
        )
        source_path = None if matching_name is None else Path(source_paths[matching_name])
        parser_error = None if matching_name is None else (load_errors or {}).get(matching_name)
        if parser_error is not None:
            warning = (
                f"{component_name} STL at {source_path} could not be parsed ({parser_error}); "
                "showing the equivalent solver mesh for this component."
            )
        elif source_path is not None:
            warning = (
                f"{component_name} STL at {source_path} was not loaded; showing the "
                "equivalent solver mesh for this component."
            )
        else:
            warning = (
                f"{component_name} STL was not found in the selected assembly path; "
                "showing the equivalent solver mesh for this component."
            )
        return FeaDisplaySelection(
            mode="Solver mesh",
            figure=build_fea_figure(component, phase_index, component_name, colorscale),
            mapped_fields=None,
            source_path=source_path,
            geometry_description=None,
            warning=warning,
        )

    source_path = _source_path_for_part(display_part, source_paths)
    try:
        normalized = normalize_stl_part(display_part)
        mapped = map_fea_fields_to_stl(
            normalized.part,
            component,
            component_name,
            source_path=source_path,
            geometry_metadata=normalized.metadata,
        )
        figure = build_mapped_fea_figure(
            mapped,
            phase_index,
            component_name,
            context_parts=assembly_parts,
            colorscale=colorscale,
        )
    except (IndexError, TypeError, ValueError) as error:
        warning = (
            f"{component_name} STL mapping failed ({error}); showing the equivalent "
            "solver mesh for this component."
        )
        return FeaDisplaySelection(
            mode="Solver mesh",
            figure=build_fea_figure(component, phase_index, component_name, colorscale),
            mapped_fields=None,
            source_path=source_path,
            geometry_description=None,
            warning=warning,
        )

    return FeaDisplaySelection(
        mode="STL-mapped results",
        figure=figure,
        mapped_fields=mapped,
        source_path=source_path,
        geometry_description=normalized.source_scale_description,
        warning=mapping_warning_message(mapped),
    )


def _convergence_status(package, component_name: str) -> str:
    convergence = package.convergence.get(component_name)
    if convergence is None:
        return "mesh sensitivity not evaluated"
    return "mesh sensitivity converged" if convergence.converged else "mesh sensitivity unresolved"


def format_selected_fea_report(
    package_path: Path,
    package,
    inputs: EngineeringInputs,
    analytical,
    component_name: str,
    phase_degrees: float,
    metrics: FeaPhysicalMetrics,
    display_selection: FeaDisplaySelection | None = None,
) -> str:
    component = getattr(package, component_name)
    material = inputs.drum_material if component_name == "drum" else inputs.shaft_material
    objective_by_component = {
        "shaft": (
            "Estimate linear-static shaft response under design torque, chain force, "
            "and the user-specified transverse reaction."
        ),
        "gear": "Estimate linear-static gear/hub response under design torque transfer.",
        "drum": (
            "Estimate quasi-static drum response to simplified retained-water gravity "
            f"and pressure at the {phase_degrees:.0f} degree phase."
        ),
    }
    boundary_by_component = {
        "shaft": (
            "Coupling-end face fixed; distributed torque plus chain and transverse-reaction "
            "tractions applied at their shaft load stations."
        ),
        "gear": "Hub interface fixed; design torque applied as distributed pitch-region traction.",
        "drum": (
            "Coupling region fixed; reduced-order pressure and retained-water gravity applied "
            "as a quasi-static phase load."
        ),
    }
    geometry_by_component = {
        "shaft": (
            f"Shaft d={inputs.shaft_diameter_m * 1000.0:.3f} mm, "
            f"L={inputs.shaft_length_m * 1000.0:.3f} mm; chain station "
            f"{inputs.chain_force_overhang_m * 1000.0:.3f} mm; reaction station "
            f"{inputs.shaft_reaction_overhang_m * 1000.0:.3f} mm."
        ),
        "gear": (
            f"Pitch radius={inputs.gear_pitch_radius_m * 1000.0:.3f} mm; sprocket "
            f"thickness={inputs.gear_sprocket_thickness_m * 1000.0:.3f} mm; hub radius="
            f"{inputs.gear_hub_radius_m * 1000.0:.3f} mm; hub thickness="
            f"{inputs.gear_hub_thickness_m * 1000.0:.3f} mm."
        ),
        "drum": (
            f"Drum R={inputs.drum_radius_m * 1000.0:.3f} mm, depth="
            f"{inputs.drum_depth_m * 1000.0:.3f} mm, equivalent wall="
            f"{inputs.drum_wall_thickness_m * 1000.0:.3f} mm; open area="
            f"{inputs.perforation_open_area_ratio:.1%}; effective stiffness factor="
            f"{inputs.drum_stiffness_factor:.3f}."
        ),
    }
    factor_text = (
        "N/A" if metrics.minimum_factor_of_safety is None else f"{metrics.minimum_factor_of_safety:.3f}"
    )
    shear_text = "N/A" if metrics.maximum_shear_pa is None else f"{metrics.maximum_shear_pa / 1.0e6:.3f} MPa"
    convergence = package.convergence.get(component_name)
    sensitivity_lines = [f"Status: {_convergence_status(package, component_name)}"]
    if convergence is not None:
        sensitivity_lines.extend(
            [
                (
                    f"Medium: {convergence.medium.node_count:,} nodes, "
                    f"{convergence.medium.element_count:,} elements; P95="
                    f"{convergence.medium.stress_95th_percentile_pa / 1.0e6:.3f} MPa; "
                    f"displacement={convergence.medium.maximum_displacement_m * 1000.0:.4f} mm"
                ),
                (
                    f"Fine: {convergence.fine.node_count:,} nodes, "
                    f"{convergence.fine.element_count:,} elements; P95="
                    f"{convergence.fine.stress_95th_percentile_pa / 1.0e6:.3f} MPa; "
                    f"displacement={convergence.fine.maximum_displacement_m * 1000.0:.4f} mm"
                ),
                f"Stress change: {convergence.stress_change_fraction * 100.0:.2f}%",
                f"Displacement change: {convergence.displacement_change_fraction * 100.0:.2f}%",
            ]
        )

    if component_name == "shaft":
        difference = (
            (metrics.maximum_von_mises_pa - analytical.shaft_von_mises_pa)
            / analytical.shaft_von_mises_pa
            * 100.0
        )
        comparison = (
            "Shaft analytical-vs-FEA stress difference: "
            f"{difference:+.2f}% (FEA physical maximum versus closed-form shaft von Mises; "
            "positive means FEA is higher)."
        )
    else:
        comparison = (
            "Analytical-vs-FEA stress difference: N/A; this component has no direct "
            "closed-form stress comparator in the Stage 1 analytical model."
        )

    display_lines: list[str] = []
    if (
        display_selection is not None
        and display_selection.mode == "STL-mapped results"
        and display_selection.mapped_fields is not None
    ):
        mapped = display_selection.mapped_fields
        registration = mapped.metadata["registration"]
        radial = registration["radial_scale_factors"]
        projection = mapped.metadata["projection_error_m"]
        source_name = (
            display_selection.source_path.name
            if display_selection.source_path is not None
            else "unrecorded STL source"
        )
        display_lines = [
            "",
            "STL-Mapped Visualization",
            "------------------------",
            "Physical Stage 1 FEA fields mapped onto authoritative STL display geometry.",
            f"STL source: {source_name}",
            f"Applied display geometry: {display_selection.geometry_description}",
            (
                f"Display STL: {len(mapped.vertices_m):,} nodes, "
                f"{len(mapped.faces):,} triangles."
            ),
            (
                "Registration scale factors: "
                f"X={registration['longitudinal_scale_factor']:.6f}, "
                f"Y={radial['y']:.6f}, Z={radial['z']:.6f}"
            ),
            (
                "Registration maximum mismatch: "
                f"{registration['scale_mismatch']['maximum_fraction'] * 100.0:.2f}%"
            ),
            (
                "Projection error: "
                f"median={projection['median'] * 1000.0:.3f} mm, "
                f"P95={projection['p95'] * 1000.0:.3f} mm, "
                f"max={projection['max'] * 1000.0:.3f} mm"
            ),
            (
                "Field projection domain: "
                f"{mapped.metadata.get('projection_domain', 'full exterior surface')} "
                f"({mapped.metadata.get('solver_projection_node_count', 0):,} solver nodes)"
            ),
            f"Registration warning triggered: {'YES' if registration['warning'] else 'NO'}",
            (
                "The STL remains undeformed; displacement is reported in hover values and "
                "is not visually amplified."
            ),
        ]
    elif display_selection is not None:
        display_lines = [
            "",
            "Visualization Geometry",
            "----------------------",
            "Equivalent solver boundary mesh displayed.",
        ]
        if display_selection.warning:
            display_lines.append(f"STL fallback reason: {display_selection.warning}")

    package_summary = "\n".join(
        [
            "Objective and Load Case",
            "-----------------------",
            objective_by_component[component_name],
            f"Selected component: {component_name}; solved phase: {phase_degrees:.0f} deg.",
            f"Result package: {package_path} (schema {package.schema_version}).",
            "",
            "Geometry and Assumed Materials",
            "------------------------------",
            geometry_by_component[component_name],
            (
                f"{material.name}: E={material.youngs_modulus_pa / 1.0e9:.3f} GPa, "
                f"nu={material.poisson_ratio:.4f}, density={material.density_kg_m3:.1f} kg/m3, "
                f"yield={material.yield_strength_pa / 1.0e6:.3f} MPa."
            ),
            "Enclosure dimensions are reported context only; the enclosure is not solved.",
            "",
            "Boundary Conditions",
            "-------------------",
            boundary_by_component[component_name],
            "",
            "Mesh and Sensitivity",
            "--------------------",
            f"Solver mesh: {metrics.node_count:,} nodes, {metrics.element_count:,} tetrahedral elements.",
            *sensitivity_lines,
            *display_lines,
            "",
            "FEA Results",
            "-----------",
            f"Physical maximum von Mises stress: {metrics.maximum_von_mises_pa / 1.0e6:.3f} MPa",
            f"Physical maximum displacement: {metrics.maximum_displacement_m * 1000.0:.4f} mm",
            f"Maximum shear: {shear_text}",
            f"Minimum factor of safety: {factor_text}",
            "",
            "Analytical Comparison",
            "---------------------",
            comparison,
            "",
            "Model Limitations",
            "-----------------",
            (
                "Estimated simplified linear-static FEA with quasi-static load phases and "
                "reduced-order water loading; not CFD, transient dynamics, fatigue, buckling, "
                "contact, weld, or enclosure analysis. Local peaks remain mesh-sensitive."
            ),
            (
                "Individual perforation-edge stress concentrations are not resolved; the "
                "equivalent solver represents perforations through open-area, relief, and "
                "stiffness assumptions."
            ),
        ]
    )
    return format_fea_engineering_summary(
        inputs, analytical, package_summary=package_summary
    )


def _payload_registration_warnings(payload, captured_warnings) -> tuple[str, ...]:
    messages = [str(item.message) for item in captured_warnings]
    for part in payload.parts:
        mapping = part.metadata.get("fea_mapping")
        if isinstance(mapping, Mapping) and mapping.get("warning"):
            message = mapping.get("warning_message")
            if message:
                messages.append(str(message))
            registration = mapping.get("registration")
            if isinstance(registration, Mapping) and registration.get("warning_message"):
                messages.append(str(registration["warning_message"]))
    return tuple(dict.fromkeys(messages))


def _render_animation_export(
    *,
    export_requested: bool,
    parts: Sequence[AssemblyPart],
    specs: Sequence[StlPartSpec],
    inputs: EngineeringInputs,
    analytical,
    selection: AnimationExportSelection,
    package_path: Path | None,
    colorscale: str,
    rotation_axis: Sequence[float],
) -> None:
    """Build or reuse one exact-fingerprint HTML export and expose it in-app."""

    missing = missing_animation_parts(parts)
    if missing:
        if export_requested:
            st.error(
                "HTML animation export needs real STL geometry for: "
                + ", ".join(missing)
                + ". Select a folder containing the complete rotating assembly."
            )
        return

    try:
        source_fingerprints = animation_source_fingerprints(parts, specs)
        package_fingerprint = (
            None
            if selection.package is None or package_path is None
            else animation_package_content_fingerprint(package_path, selection.package)
        )
        fingerprint = animation_export_fingerprint(
            parts=parts,
            source_fingerprints=source_fingerprints,
            inputs=inputs,
            analysis_mode=selection.analysis_mode,
            package_fingerprint=package_fingerprint,
            colorscale=colorscale,
            rotation_axis=rotation_axis,
            renderer_fingerprint=renderer_asset_fingerprint(),
        )
    except (OSError, TypeError, ValueError) as error:
        if export_requested:
            st.error(f"HTML animation export inputs are unavailable: {error}")
        return

    output_path = animation_export_output_path(PROJECT_ROOT)
    previous_state = st.session_state.get(ANIMATION_EXPORT_SESSION_KEY)
    if export_requested and not can_reuse_animation_export(
        previous_state, fingerprint, output_path
    ):
        try:
            with st.spinner("Building self-contained HTML animation..."):
                with warnings.catch_warnings(record=True) as captured:
                    warnings.simplefilter("always")
                    payload = build_animation_export_payload(
                        parts=parts,
                        inputs=inputs,
                        analytical=analytical,
                        selection=selection,
                        colorscale=colorscale,
                        rotation_axis=rotation_axis,
                    )
                registration_warnings = _payload_registration_warnings(
                    payload, captured
                )
                exported_path = export_cyclewash_animation_html(payload, output_path)
                candidate_state = animation_export_state(
                    fingerprint,
                    exported_path,
                    selection.analysis_mode,
                    registration_warnings,
                )
        except (OSError, TypeError, ValueError) as error:
            st.session_state[ANIMATION_EXPORT_SESSION_KEY] = updated_animation_export_state(
                previous_state, None, export_succeeded=False
            )
            st.error(
                "HTML animation export failed; the prior file and session selection were kept. "
                f"Details: {error}"
            )
            return
        st.session_state[ANIMATION_EXPORT_SESSION_KEY] = updated_animation_export_state(
            previous_state, candidate_state, export_succeeded=True
        )

    state = st.session_state.get(ANIMATION_EXPORT_SESSION_KEY)
    if not can_reuse_animation_export(state, fingerprint, output_path):
        return
    file_size = int(state["size_bytes"])
    generated_mode = (
        "FEA-backed" if selection.analysis_mode == "fea" else "Geometric preview"
    )
    st.success(
        f"{generated_mode} HTML animation: {output_path.resolve()} "
        f"({file_size:,} bytes)"
    )
    warning_label = "registration warnings"
    registration_warnings = tuple(state.get("registration_warnings", ()))
    if registration_warnings:
        st.warning(
            f"{warning_label}:\n- " + "\n- ".join(registration_warnings)
        )
    else:
        st.caption(f"Generated mode: {generated_mode}; {warning_label}: none.")
    st.download_button(
        "Download HTML Animation",
        data=output_path.read_bytes(),
        file_name=output_path.name,
        mime="text/html",
        key=f"download_cyclewash_animation_{fingerprint}",
    )


def _render_fea_visualizer() -> None:
    defaults = EngineeringInputs()
    fea_rotation_axis = (1.0, 0.0, 0.0)
    with st.sidebar:
        with st.expander("Animation assembly", expanded=False):
            animation_stl_input = st.text_input(
                "Animation STL folder or file",
                value=str(DEFAULT_STL_DIRECTORY),
                help="Export loads every STL from this real assembly source; demonstration geometry is never substituted.",
            )
            fea_animation_colorscale = st.selectbox(
                "FEA stress / animation colorscale",
                ("Turbo", "Viridis", "Jet", "Rainbow"),
            )
            st.caption("Animation rotation axis: Blender X / shaft axis.")
        with st.expander("Geometry assumptions", expanded=False):
            enclosure_width_mm = st.number_input(
                "Enclosure width (mm)", min_value=1.0, value=defaults.enclosure_width_m * 1000.0
            )
            enclosure_body_height_mm = st.number_input(
                "Enclosure body height (mm)",
                min_value=1.0,
                value=defaults.enclosure_body_height_m * 1000.0,
            )
            overall_height_mm = st.number_input(
                "Overall height (mm)", min_value=1.0, value=defaults.overall_height_m * 1000.0
            )
            drum_radius_mm = st.number_input(
                "Drum radius (mm)", min_value=1.0, value=defaults.drum_radius_m * 1000.0
            )
            drum_depth_mm = st.number_input(
                "Drum depth (mm)", min_value=1.0, value=defaults.drum_depth_m * 1000.0
            )
            drum_wall_mm = st.number_input(
                "Equivalent drum wall (mm)",
                min_value=0.1,
                value=defaults.drum_wall_thickness_m * 1000.0,
                step=0.1,
            )
            shaft_diameter_mm = st.number_input(
                "Shaft diameter (mm)", min_value=1.0, value=defaults.shaft_diameter_m * 1000.0
            )
            shaft_length_mm = st.number_input(
                "Shaft modeled length (mm)", min_value=1.0, value=defaults.shaft_length_m * 1000.0
            )
            chain_station_mm = st.number_input(
                "Chain load station (mm)",
                min_value=1.0,
                value=defaults.chain_force_overhang_m * 1000.0,
            )
            reaction_station_mm = st.number_input(
                "Reaction load station (mm)",
                min_value=1.0,
                value=defaults.shaft_reaction_overhang_m * 1000.0,
            )
            gear_pitch_radius_mm = st.number_input(
                "Gear pitch radius (mm)",
                min_value=1.0,
                value=defaults.gear_pitch_radius_m * 1000.0,
            )
            sprocket_thickness_mm = st.number_input(
                "Sprocket thickness (mm)",
                min_value=0.1,
                value=defaults.gear_sprocket_thickness_m * 1000.0,
                step=0.1,
            )
            hub_radius_mm = st.number_input(
                "Hub radius (mm)", min_value=0.1, value=defaults.gear_hub_radius_m * 1000.0
            )
            hub_thickness_mm = st.number_input(
                "Hub thickness (mm)",
                min_value=0.1,
                value=defaults.gear_hub_thickness_m * 1000.0,
                step=0.1,
            )
            perforation_open_area = st.slider(
                "Perforation open area (%)",
                0,
                100,
                int(defaults.perforation_open_area_ratio * 100),
                1,
            ) / 100.0
            drum_stiffness_factor = st.slider(
                "Perforated drum stiffness factor",
                min_value=0.10,
                max_value=1.00,
                value=defaults.drum_stiffness_factor,
                step=0.05,
            )

        with st.expander("Material assumptions", expanded=False):
            st.caption("Shaft and gear: galvanized steel")
            shaft_e_gpa = st.number_input(
                "Shaft/gear E (GPa)",
                min_value=0.1,
                value=defaults.shaft_material.youngs_modulus_pa / 1.0e9,
            )
            shaft_nu = st.number_input(
                "Shaft/gear Poisson ratio",
                min_value=0.001,
                max_value=0.499,
                value=defaults.shaft_material.poisson_ratio,
                step=0.01,
            )
            shaft_density = st.number_input(
                "Shaft/gear density (kg/m3)",
                min_value=1.0,
                value=defaults.shaft_material.density_kg_m3,
            )
            shaft_yield_mpa = st.number_input(
                "Shaft/gear yield (MPa)",
                min_value=0.1,
                value=defaults.shaft_material.yield_strength_pa / 1.0e6,
            )
            st.caption("Drum: stainless steel")
            drum_e_gpa = st.number_input(
                "Drum E (GPa)",
                min_value=0.1,
                value=defaults.drum_material.youngs_modulus_pa / 1.0e9,
            )
            drum_nu = st.number_input(
                "Drum Poisson ratio",
                min_value=0.001,
                max_value=0.499,
                value=defaults.drum_material.poisson_ratio,
                step=0.01,
            )
            drum_density = st.number_input(
                "Drum density (kg/m3)", min_value=1.0, value=defaults.drum_material.density_kg_m3
            )
            drum_yield_mpa = st.number_input(
                "Drum yield (MPa)",
                min_value=0.1,
                value=defaults.drum_material.yield_strength_pa / 1.0e6,
            )

        with st.expander("Operating loads", expanded=False):
            power_w = st.number_input(
                "Human power (W)", min_value=1.0, value=defaults.human_power_w, step=5.0
            )
            speed_rpm = st.number_input(
                "Operating speed (RPM)", min_value=1.0, value=defaults.speed_rpm, step=5.0
            )
            transient_factor = st.number_input(
                "Transient design factor",
                min_value=0.1,
                value=defaults.transient_factor,
                step=0.1,
            )
            water_density = st.number_input(
                "Water density (kg/m3)", min_value=1.0, value=defaults.water_density_kg_m3
            )
            gravity = st.number_input(
                "Gravity (m/s2)", min_value=0.1, value=defaults.gravity_m_s2, step=0.01
            )
            fill_fraction = st.slider(
                "Drum fill (%)", 0, 100, int(defaults.fill_fraction * 100), 1
            ) / 100.0
            perforation_relief = st.slider(
                "Perforation relief (%)",
                0,
                100,
                int(defaults.perforation_relief * 100),
                1,
            ) / 100.0
            slosh_amplification = st.number_input(
                "Slosh amplification",
                min_value=0.1,
                value=defaults.slosh_amplification,
                step=0.05,
            )
            suggested_reaction_n = (
                math.pi
                * (float(drum_radius_mm) / 1000.0) ** 2
                * (float(drum_depth_mm) / 1000.0)
                * float(fill_fraction)
                * (1.0 - float(perforation_relief))
                * float(water_density)
                * float(gravity)
            )
            shaft_transverse_reaction = st.number_input(
                "Suggested shaft transverse reaction (N)",
                min_value=0.0,
                value=float(suggested_reaction_n),
                help=(
                    "Suggested reaction equals the current retained-water weight. "
                    "The canonical EngineeringInputs default remains 0 N."
                ),
            )
            st.caption(
                f"Default is the current retained-water weight ({suggested_reaction_n:.1f} N); "
                "edit when support-load information is available."
            )

        with st.expander("Mesh controls", expanded=False):
            mesh_choice = st.selectbox(
                "Mesh request",
                ("Coarse", "Medium", "Medium + fine convergence"),
                index=0,
            )
            st.caption("Solves start only when the run command is pressed.")

    animation_stl_path = Path(animation_stl_input.strip().strip('"')).expanduser()
    animation_specs = _load_specs_from_path(animation_stl_path)
    animation_parts: list[AssemblyPart] = []
    animation_load_errors: dict[str, str] = {}
    for spec in animation_specs:
        try:
            animation_parts.append(load_stl_part(spec))
        except Exception as error:
            animation_load_errors[spec.name] = str(error)
    missing_export_geometry = missing_animation_parts(animation_parts)
    if animation_load_errors:
        st.warning(
            "Some animation STL parts could not be loaded: "
            + "; ".join(
                f"{name}: {error}" for name, error in animation_load_errors.items()
            )
        )
    if missing_export_geometry:
        st.warning(
            "HTML animation needs real STL parts for: "
            + ", ".join(missing_export_geometry)
            + f". Check {animation_stl_path}."
        )

    mesh_levels_by_choice = {
        "Coarse": ("coarse",),
        "Medium": ("medium",),
        "Medium + fine convergence": ("medium", "fine"),
    }
    mesh_levels = mesh_levels_by_choice[mesh_choice]
    shaft_material = MaterialProperties(
        name=defaults.shaft_material.name,
        youngs_modulus_pa=float(shaft_e_gpa) * 1.0e9,
        poisson_ratio=float(shaft_nu),
        density_kg_m3=float(shaft_density),
        yield_strength_pa=float(shaft_yield_mpa) * 1.0e6,
    )
    drum_material = MaterialProperties(
        name=defaults.drum_material.name,
        youngs_modulus_pa=float(drum_e_gpa) * 1.0e9,
        poisson_ratio=float(drum_nu),
        density_kg_m3=float(drum_density),
        yield_strength_pa=float(drum_yield_mpa) * 1.0e6,
    )
    inputs = EngineeringInputs(
        human_power_w=float(power_w),
        speed_rpm=float(speed_rpm),
        transient_factor=float(transient_factor),
        enclosure_width_m=float(enclosure_width_mm) / 1000.0,
        enclosure_body_height_m=float(enclosure_body_height_mm) / 1000.0,
        overall_height_m=float(overall_height_mm) / 1000.0,
        shaft_diameter_m=float(shaft_diameter_mm) / 1000.0,
        shaft_length_m=float(shaft_length_mm) / 1000.0,
        chain_force_overhang_m=float(chain_station_mm) / 1000.0,
        shaft_transverse_reaction_n=float(shaft_transverse_reaction),
        shaft_reaction_overhang_m=float(reaction_station_mm) / 1000.0,
        gear_pitch_radius_m=float(gear_pitch_radius_mm) / 1000.0,
        gear_sprocket_thickness_m=float(sprocket_thickness_mm) / 1000.0,
        gear_hub_radius_m=float(hub_radius_mm) / 1000.0,
        gear_hub_thickness_m=float(hub_thickness_mm) / 1000.0,
        drum_radius_m=float(drum_radius_mm) / 1000.0,
        drum_depth_m=float(drum_depth_mm) / 1000.0,
        drum_wall_thickness_m=float(drum_wall_mm) / 1000.0,
        fill_fraction=float(fill_fraction),
        perforation_relief=float(perforation_relief),
        perforation_open_area_ratio=float(perforation_open_area),
        drum_stiffness_factor=float(drum_stiffness_factor),
        slosh_amplification=float(slosh_amplification),
        water_density_kg_m3=float(water_density),
        gravity_m_s2=float(gravity),
        shaft_material=shaft_material,
        drum_material=drum_material,
    )
    try:
        analytical = calculate_engineering_loads(inputs)
    except (TypeError, ValueError) as error:
        st.error(f"Engineering inputs are inconsistent: {error}")
        return
    expected_path = fea_package_path(PROJECT_ROOT, inputs, mesh_levels)

    st.subheader("Solver status")
    status = detect_fea_solver(PROJECT_ROOT)
    if status.available:
        version_text = ", ".join(f"{name} {version}" for name, version in status.versions.items())
        st.success(f"{status.message} {version_text}")
    else:
        st.caption("Optional local Stage 1 FEA solver is not installed in this environment.")

    cached_package_available = False
    if (expected_path / "summary.json").is_file():
        try:
            require_matching_package(
                load_stage1_package(expected_path), inputs, mesh_levels
            )
        except (OSError, ValueError) as error:
            st.warning(f"Request-path FEA cache was rejected: {error}")
        else:
            cached_package_available = True
    if cached_package_available:
        st.caption(f"A request-path cache is available at {expected_path}.")

    action_state = resolve_fea_action_state(
        cached_package_available=cached_package_available,
        solver_available=status.available,
    )
    if action_state.mode == "analytical":
        st.info(action_state.notice)
        action_requested = False
    else:
        st.caption(action_state.notice)
        assert action_state.action_label is not None
        action_requested = st.button(action_state.action_label, type="primary")

    if action_requested:
        previous_path = st.session_state.get(SELECTED_FEA_PACKAGE_SESSION_KEY)
        progress_bar = st.progress(0.0, text="0% - Preparing Stage 1 FEA")
        live_status = st.status("Preparing Stage 1 FEA", expanded=False)
        progress_adapter = StreamlitFeaProgress(progress_bar, live_status)
        try:
            if cached_package_available:
                try:
                    package = require_matching_package(
                        load_stage1_package(expected_path), inputs, mesh_levels
                    )
                except (OSError, ValueError):
                    if not status.available:
                        raise
                    package = run_fea_subprocess(
                        inputs,
                        FEA_RESULT_ROOT,
                        mesh_levels,
                        progress_callback=progress_adapter,
                    )
            else:
                package = run_fea_subprocess(
                    inputs,
                    FEA_RESULT_ROOT,
                    mesh_levels,
                    progress_callback=progress_adapter,
                )
            require_matching_package(package, inputs, mesh_levels)
            require_matching_package(
                load_stage1_package(expected_path), inputs, mesh_levels
            )
        except (FeaRunnerError, OSError, ValueError) as error:
            st.session_state[SELECTED_FEA_PACKAGE_SESSION_KEY] = updated_selected_package_path(
                previous_path, expected_path, solve_succeeded=False
            )
            progress_adapter.fail(f"Stage 1 FEA failed: {error}")
            st.error(f"Stage 1 FEA did not produce a selectable package: {error}")
        else:
            st.session_state[SELECTED_FEA_PACKAGE_SESSION_KEY] = str(
                updated_selected_package_path(
                    previous_path, expected_path, solve_succeeded=True
                )
            )
            progress_adapter.complete()

    selected_path = package_path_for_source(
        "Simplified Stage 1 FEA",
        st.session_state.get(SELECTED_FEA_PACKAGE_SESSION_KEY),
        expected_path,
    )
    package = None
    package_load_error = None
    if selected_path is not None:
        try:
            package = require_matching_package(
                load_stage1_package(selected_path), inputs, mesh_levels
            )
        except (OSError, ValueError) as error:
            package_load_error = error

    export_selection = resolve_animation_export_mode(
        "Simplified Stage 1 FEA", package, inputs, mesh_levels
    )
    st.caption(export_selection.caption)
    fea_phase_column, fea_export_column = st.columns([1.8, 1.0])
    with fea_phase_column:
        requested_phase = st.slider(
            "Animation / drum phase (deg)", 0, 359, 0, 1
        )
    with fea_export_column:
        fea_export_requested = st.button("Export HTML Animation", disabled=not animation_geometry_ready(animation_parts), key="fea_html_animation_export")

    _render_animation_export(
        export_requested=fea_export_requested,
        parts=animation_parts,
        specs=animation_specs,
        inputs=inputs,
        analytical=analytical,
        selection=export_selection,
        package_path=(selected_path if export_selection.analysis_mode == "fea" else None),
        colorscale=fea_animation_colorscale,
        rotation_axis=fea_rotation_axis,
    )

    if package is None:
        if package_load_error is not None:
            st.error(
                "Selected FEA package was rejected for visualization; HTML export will use "
                f"the geometric preview fallback. Details: {package_load_error}"
            )
            return
        if action_state.mode == "analytical":
            st.subheader("Analytical preview")
            figure, analytical_summary = build_stage1_analytical_preview(
                animation_parts,
                inputs,
                analytical,
                fea_animation_colorscale,
                float(requested_phase),
            )
            viewer_column, summary_column = st.columns([1.45, 1.0], gap="large")
            with viewer_column:
                st.plotly_chart(figure, width="stretch")
            with summary_column:
                st.subheader("Analytical Calculation Summary")
                st.code(analytical_summary, language="text", wrap_lines=True)
            return
        st.info("Load the exact cached request or run the local solver to view physical FEA results.")
        return

    component_name = st.segmented_control(
        "Physical FEA component", ("shaft", "gear", "drum"), default="drum"
    )
    display_mode = st.selectbox("Display geometry", FEA_DISPLAY_MODES, index=0)
    component = getattr(package, component_name)
    phase_index, phase_degrees = select_fea_phase(
        component.phase_degrees, float(requested_phase)
    )
    if component_name == "drum":
        st.caption(f"Displaying nearest solved 30-degree phase: {phase_degrees:.0f} deg.")

    metrics = fea_physical_metrics(component, phase_index)
    convergence_text = _convergence_status(package, component_name)
    metric_columns = st.columns(5)
    metric_columns[0].metric("Max stress", f"{metrics.maximum_von_mises_pa / 1.0e6:.3f} MPa")
    metric_columns[1].metric("Max displacement", f"{metrics.maximum_displacement_m * 1000.0:.4f} mm")
    metric_columns[2].metric(
        "Min FoS",
        "N/A" if metrics.minimum_factor_of_safety is None else f"{metrics.minimum_factor_of_safety:.3f}",
    )
    metric_columns[3].metric("Mesh", f"{metrics.node_count:,} / {metrics.element_count:,}")
    metric_columns[4].metric("Convergence", convergence_text)
    st.caption("Mesh count is solver nodes / tetrahedra. Mapped STL geometry remains undeformed.")

    source_paths = {
        spec.name: Path(spec.source)
        for spec in animation_specs
        if isinstance(spec.source, (str, Path))
    }
    display_selection = resolve_fea_display(
        component=component,
        phase_index=phase_index,
        component_name=component_name,
        requested_mode=display_mode,
        colorscale=fea_animation_colorscale,
        assembly_parts=animation_parts,
        source_paths=source_paths,
        load_errors=animation_load_errors,
    )
    if display_selection.warning:
        st.warning(display_selection.warning)
    figure = display_selection.figure
    engineering_summary = format_selected_fea_report(
        selected_path,
        package,
        inputs,
        analytical,
        component_name,
        phase_degrees,
        metrics,
        display_selection=display_selection,
    )
    viewer_column, summary_column = st.columns([1.45, 1.0], gap="large")
    with viewer_column:
        st.plotly_chart(figure, width="stretch")
    with summary_column:
        st.subheader("Stage 1 Engineering Summary")
        st.code(engineering_summary, language="text", wrap_lines=True)


def render_structural_visualizer() -> None:
    """Render the structural stress visualizer inside an existing Streamlit app."""
    st.title("CycleWash Structural Load Visualizer")
    analysis_source = st.segmented_control(
        "Analysis source", ANALYSIS_SOURCES, default="Geometric preview"
    )
    if analysis_source == "Simplified Stage 1 FEA":
        _render_fea_visualizer()
        return

    with st.sidebar:
        st.header("Assembly Source")
        stl_path_input = st.text_input(
            "STL folder or file path",
            value=str(DEFAULT_STL_DIRECTORY),
            help="Use a folder to load every .stl in it, or use a direct path to one .stl file.",
        )

    stl_path = Path(stl_path_input.strip().strip('"')).expanduser()
    specs = _load_specs_from_path(stl_path)

    if len(specs) == 0:
        if stl_path.exists():
            st.info(f"No STL files were found at {stl_path}.")
        else:
            st.warning(f"STL path does not exist: {stl_path}")
        return

    loaded_parts: list[AssemblyPart] = []
    for spec in specs:
        try:
            loaded_parts.append(load_stl_part(spec))
        except Exception as error:
            st.error(f"Could not load {spec.name}: {error}")

    if len(loaded_parts) == 0:
        return

    rotation_origin = infer_rotating_axis_origin(loaded_parts)
    has_shaft = any("shaft" in _normalize_part_name(part.name) for part in loaded_parts)
    door_parts = [part for part in loaded_parts if _is_door_part(part.name)]
    default_door_hinge_origin = (
        _estimate_door_hinge_origin(door_parts[0], "min X")
        if door_parts
        else (0.0, 0.0, 0.0)
    )

    with st.sidebar:
        st.header("Stress View")
        load_case = st.selectbox(
            "Load case",
            [
                "Spin + water circulation through drum holes",
                "Rotating drum / gear / shaft torsion",
                "Water load inside stationary drum",
                "Door hinge closing load",
            ],
        )
        colorscale = st.selectbox("Stress color scale", ["Turbo", "Viridis", "Jet", "Rainbow"])

        rotating_case = load_case in {
            "Spin + water circulation through drum holes",
            "Rotating drum / gear / shaft torsion",
        }
        water_case = load_case in {
            "Spin + water circulation through drum holes",
            "Water load inside stationary drum",
        }
        door_case = load_case == "Door hinge closing load"

        drum_speed_rpm = 0.0
        simulation_time_s = 0.0
        phase_offset_degrees = 0.0
        water_fill_fraction = 0.35
        perforation_relief = 0.45
        casing_axis = (0.0, 0.0, -1.0)
        rotation_axis = (1.0, 0.0, 0.0)
        door_opening_degrees = 0.0
        door_axis = (0.0, 0.0, -1.0)
        door_hinge_origin = default_door_hinge_origin
        geometric_export_requested = False

        if rotating_case:
            st.header("Rotating Assembly")
            drum_speed_rpm = st.number_input(
                "Inner drum / shaft / gear speed (RPM)",
                min_value=0.0,
                max_value=300.0,
                value=60.0,
                step=5.0,
            )
            simulation_time_s = st.number_input(
                "Simulation time (s)",
                min_value=0.0,
                max_value=60.0,
                value=0.0,
                step=0.1,
            )
            phase_column, export_column = st.columns([1.8, 1.0])
            with phase_column:
                phase_offset_degrees = st.slider(
                    "Phase offset (deg)",
                    min_value=0.0,
                    max_value=360.0,
                    value=0.0,
                    step=5.0,
                )
            with export_column:
                geometric_export_requested = st.button("Export HTML Animation", disabled=not animation_geometry_ready(loaded_parts), key="geometric_html_animation_export")

        if water_case:
            st.header("Water Load")
            water_fill_fraction = st.slider(
                "Water fill inside drum (%)",
                min_value=0,
                max_value=100,
                value=35,
                step=5,
            ) / 100.0
            perforation_relief = st.slider(
                "Drum through-hole circulation relief (%)",
                min_value=0,
                max_value=100,
                value=45,
                step=5,
            ) / 100.0

        rotation_angle_degrees = (
            drum_speed_rpm * 360.0 * simulation_time_s / 60.0 + phase_offset_degrees
        ) % 360.0

        with st.expander("Load direction axes", expanded=False):
            st.caption(
                "Axes are direction vectors. The shaft origin is inferred from the STL geometry."
            )
            if water_case:
                casing_axis = (
                    st.number_input("Casing/load axis X", value=0.0, step=0.1),
                    st.number_input("Casing/load axis Y", value=0.0, step=0.1),
                    st.number_input("Casing/load axis Z", value=-1.0, step=0.1),
                )
            if rotating_case:
                rotation_axis_preset = st.selectbox(
                    "Rotation axis preset",
                    ["Blender X / shaft axis", "Blender Y", "Blender Z", "Custom"],
                )
                preset_axes = {
                    "Blender X / shaft axis": (1.0, 0.0, 0.0),
                    "Blender Y": (0.0, 1.0, 0.0),
                    "Blender Z": (0.0, 0.0, 1.0),
                    "Custom": (1.0, 0.0, 0.0),
                }
                if rotation_axis_preset == "Custom":
                    rotation_axis = (
                        st.number_input("Custom rotation axis X", value=1.0, step=0.1),
                        st.number_input("Custom rotation axis Y", value=0.0, step=0.1),
                        st.number_input("Custom rotation axis Z", value=0.0, step=0.1),
                    )
                else:
                    rotation_axis = preset_axes[rotation_axis_preset]

        if door_case:
            st.header("Door Pose")
            door_opening_degrees = st.slider(
                "Door opening angle (deg)",
                min_value=0.0,
                max_value=90.0,
                value=0.0,
                step=5.0,
            )
            door_hinge_side = st.selectbox(
                "Door hinge side",
                ["min X", "max X", "min Y", "max Y"],
            )
            door_axis_preset = st.selectbox(
                "Door hinge axis preset",
                [
                    "Blender -Z / vertical hinge",
                    "Blender -X / shaft axis",
                    "Blender Y",
                    "Custom",
                ],
            )
            door_axis_defaults = {
                "Blender -Z / vertical hinge": (0.0, 0.0, -1.0),
                "Blender -X / shaft axis": (-1.0, 0.0, 0.0),
                "Blender Y": (0.0, 1.0, 0.0),
                "Custom": (0.0, 0.0, -1.0),
            }
            if door_axis_preset == "Custom":
                door_axis = (
                    st.number_input("Custom door hinge axis X", value=0.0, step=0.1),
                    st.number_input("Custom door hinge axis Y", value=0.0, step=0.1),
                    st.number_input("Custom door hinge axis Z", value=-1.0, step=0.1),
                )
            else:
                door_axis = door_axis_defaults[door_axis_preset]
            estimated_hinge_origin = (
                _estimate_door_hinge_origin(door_parts[0], door_hinge_side)
                if door_parts
                else (0.0, 0.0, 0.0)
            )
            auto_estimate_door_hinge = st.checkbox(
                "Auto-estimate door hinge pivot",
                value=True,
            )
            door_hinge_origin = (
                estimated_hinge_origin
                if auto_estimate_door_hinge
                else (
                    st.number_input("Door hinge pivot X", value=estimated_hinge_origin[0], step=5.0),
                    st.number_input("Door hinge pivot Y", value=estimated_hinge_origin[1], step=5.0),
                    st.number_input("Door hinge pivot Z", value=estimated_hinge_origin[2], step=5.0),
                )
            )

        door_pose_angle_degrees = closed_door_pose_angle(door_opening_degrees)

    display_parts = [
        _apply_motion_pose(
            part,
            rotation_axis=rotation_axis,
            rotation_origin=rotation_origin,
            rotation_angle_degrees=rotation_angle_degrees,
            door_axis=door_axis,
            door_angle_degrees=door_pose_angle_degrees,
            door_hinge_origin=door_hinge_origin,
        )
        for part in loaded_parts
    ]
    presentation = load_case_presentation(display_parts, load_case)
    principal_parts = tuple(
        part.name for part in display_parts if presentation[part.name].heatmap
    )

    st.caption(f"Loaded {len(loaded_parts)} STL component(s) from {stl_path}.")
    if not has_shaft:
        st.info("No shaft STL was found; the rotating-group centroid is used as the pivot.")
    if not principal_parts:
        st.warning("No principal component was found for this load case.")

    metric_columns = st.columns(4)
    principal_text = ", ".join(principal_parts) or "None"
    metric_columns[0].metric("Load case", LOAD_CASE_SHORT_LABELS[load_case])
    metric_columns[1].metric("Heatmapped", f"{len(principal_parts)} part(s)")
    if door_case:
        metric_columns[2].metric("Door opening", f"{door_opening_degrees:.1f} deg")
        metric_columns[3].metric("Hinge axis", format_axis_label(door_axis))
    else:
        metric_columns[2].metric("Spin speed", f"{drum_speed_rpm:.1f} RPM")
        metric_columns[3].metric(
            "Water fill",
            f"{water_fill_fraction * 100:.0f}%" if water_case else "Not applied",
        )
    st.caption(f"Heatmapped components: {principal_text}")

    figure = build_structural_figure(
        parts=display_parts,
        presentation=presentation,
        load_case=load_case,
        colorscale=colorscale,
        casing_axis=casing_axis,
        rotation_axis=rotation_axis,
        rotation_origin=rotation_origin,
        rotation_angle_degrees=rotation_angle_degrees,
        drum_speed_rpm=drum_speed_rpm,
        water_fill_fraction=water_fill_fraction,
        perforation_relief=perforation_relief,
        door_axis=door_axis,
        door_hinge_origin=door_hinge_origin,
    )
    math_summary = format_structural_math_summary(
        load_case=load_case,
        principal_parts=principal_parts,
        drum_speed_rpm=drum_speed_rpm,
        simulation_time_s=simulation_time_s,
        phase_offset_degrees=phase_offset_degrees,
        rotation_angle_degrees=rotation_angle_degrees,
        water_fill_fraction=water_fill_fraction,
        perforation_relief=perforation_relief,
        casing_axis=casing_axis,
        rotation_axis=rotation_axis,
        rotation_origin=rotation_origin,
        door_axis=door_axis,
        door_hinge_origin=door_hinge_origin,
    )

    viewer_column, math_column = st.columns([1.45, 1.0], gap="large")
    with viewer_column:
        st.plotly_chart(figure, width="stretch")
    with math_column:
        st.subheader("Structural Load Math")
        st.code(math_summary, language="text", wrap_lines=True)
    if rotating_case:
        try:
            animation_inputs = EngineeringInputs(
                speed_rpm=float(drum_speed_rpm),
                fill_fraction=float(water_fill_fraction),
                perforation_relief=float(perforation_relief),
            )
            animation_analytical = calculate_engineering_loads(animation_inputs)
        except (TypeError, ValueError) as error:
            if geometric_export_requested:
                st.error(
                    "HTML animation needs valid rotating-preview RPM, fill, and relief inputs. "
                    f"Details: {error}"
                )
        else:
            _render_animation_export(
                export_requested=geometric_export_requested,
                parts=loaded_parts,
                specs=specs,
                inputs=animation_inputs,
                analytical=animation_analytical,
                selection=resolve_animation_export_mode(
                    "Geometric preview", None, animation_inputs, ("coarse",)
                ),
                package_path=None,
                colorscale=colorscale,
                rotation_axis=rotation_axis,
            )


def main() -> None:
    """Run the Streamlit structural stress visualizer sample."""
    st.set_page_config(page_title="CycleWash Structural Visualizer", layout="wide")
    render_structural_visualizer()


if __name__ == "__main__":
    main()
