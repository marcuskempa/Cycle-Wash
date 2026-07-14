"""Versioned, dependency-light storage for CycleWash Stage 1 FEA results.

The package format deliberately keeps report metadata as ordinary JSON while
placing numerical meshes and fields in deterministic compressed NPZ archives.
displacement_m is stored in meters.
von_mises_pa and nodal_maximum_shear_pa are stored in pascals.
element_strain and nodal_factor_of_safety are dimensionless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
import json
import math
import os
from pathlib import Path
import shutil
from sys import float_info
import tempfile
from typing import Any, Mapping
import zipfile

import numpy as np


SCHEMA_VERSION = "cyclewash-fea-v1"
_COMPONENT_NAMES = ("shaft", "gear", "drum")
_COMPONENT_FILENAMES = {
    "shaft": "shaft_results.npz",
    "gear": "gear_results.npz",
    "drum": "drum_phase_results.npz",
}
_BASE_ARRAY_NAMES = ("vertices_m", "tetrahedra", "displacement_m", "von_mises_pa")
_OPTIONAL_ARRAY_NAMES = (
    "element_strain",
    "nodal_maximum_shear_pa",
    "nodal_factor_of_safety",
)
_SUMMARY_FIELDS = {
    "analytical_values",
    "assumptions",
    "components",
    "convergence",
    "inputs",
    "schema_version",
    "solver_versions",
}


@dataclass(frozen=True)
class MeshMetrics:
    """Mesh-level values used for the medium-to-fine convergence check."""

    stress_95th_percentile_pa: float
    maximum_displacement_m: float
    node_count: int
    element_count: int

    def __post_init__(self) -> None:
        for field_name in (
            "stress_95th_percentile_pa",
            "maximum_displacement_m",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{field_name} must be a finite non-negative number")
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{field_name} must be a finite non-negative number")
        for field_name in ("node_count", "element_count"):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")


@dataclass(frozen=True)
class ComponentFieldResult:
    """Raw mesh and fields across phases.

    displacement_m is stored in meters.
    von_mises_pa and nodal_maximum_shear_pa are stored in pascals.
    element_strain and nodal_factor_of_safety are dimensionless.
    """

    vertices_m: np.ndarray
    tetrahedra: np.ndarray
    displacement_m: np.ndarray
    von_mises_pa: np.ndarray
    phase_degrees: tuple[float, ...]
    element_strain: np.ndarray | None = None
    nodal_maximum_shear_pa: np.ndarray | None = None
    nodal_factor_of_safety: np.ndarray | None = None

    def __post_init__(self) -> None:
        vertices_m = _finite_float_array(self.vertices_m, "vertices_m")
        tetrahedra = _integer_array(self.tetrahedra, "tetrahedra")
        displacement_m = _finite_float_array(self.displacement_m, "displacement_m")
        von_mises_pa = _finite_float_array(self.von_mises_pa, "von_mises_pa")
        element_strain = _optional_finite_float_array(
            self.element_strain, "element_strain"
        )
        nodal_maximum_shear_pa = _optional_finite_float_array(
            self.nodal_maximum_shear_pa, "nodal_maximum_shear_pa"
        )
        nodal_factor_of_safety = _optional_finite_float_array(
            self.nodal_factor_of_safety, "nodal_factor_of_safety"
        )

        if vertices_m.ndim != 2 or vertices_m.shape[1:] != (3,):
            raise ValueError("vertices_m must have shape (node_count, 3)")
        node_count = vertices_m.shape[0]
        if node_count == 0:
            raise ValueError("vertices_m must contain at least one node")
        if tetrahedra.ndim != 2 or tetrahedra.shape[1:] != (4,):
            raise ValueError("tetrahedra must have shape (element_count, 4)")
        if tetrahedra.size and (
            tetrahedra.min() < 0 or tetrahedra.max() >= node_count
        ):
            raise ValueError("tetrahedra contains a node index outside vertices_m")
        if displacement_m.ndim != 3 or displacement_m.shape[1:] != (node_count, 3):
            raise ValueError(
                "displacement_m must have shape (phase_count, node_count, 3)"
            )
        phase_count = displacement_m.shape[0]
        if phase_count == 0:
            raise ValueError("displacement_m must contain at least one phase")
        if von_mises_pa.shape != (phase_count, node_count):
            raise ValueError(
                "von_mises_pa must have shape (phase_count, node_count)"
            )
        if (von_mises_pa < 0.0).any():
            raise ValueError("von_mises_pa must be non-negative")
        element_count = tetrahedra.shape[0]
        if element_strain is not None and element_strain.shape != (
            phase_count,
            element_count,
            6,
        ):
            raise ValueError(
                "element_strain must have shape (phase_count, element_count, 6)"
            )
        if nodal_maximum_shear_pa is not None:
            if nodal_maximum_shear_pa.shape != (phase_count, node_count):
                raise ValueError(
                    "nodal_maximum_shear_pa must have shape (phase_count, node_count)"
                )
            if (nodal_maximum_shear_pa < 0.0).any():
                raise ValueError("nodal_maximum_shear_pa must be non-negative")
        if nodal_factor_of_safety is not None:
            if nodal_factor_of_safety.shape != (phase_count, node_count):
                raise ValueError(
                    "nodal_factor_of_safety must have shape (phase_count, node_count)"
                )
            if (nodal_factor_of_safety <= 0.0).any():
                raise ValueError("nodal_factor_of_safety must be strictly positive")

        phase_degrees = tuple(float(value) for value in self.phase_degrees)
        if len(phase_degrees) != phase_count:
            raise ValueError("phase_degrees must contain one value for each phase")
        if not all(math.isfinite(value) for value in phase_degrees):
            raise ValueError("phase_degrees must contain only finite values")

        _freeze_array(vertices_m)
        _freeze_array(tetrahedra)
        _freeze_array(displacement_m)
        _freeze_array(von_mises_pa)
        for optional_array in (
            element_strain,
            nodal_maximum_shear_pa,
            nodal_factor_of_safety,
        ):
            if optional_array is not None:
                _freeze_array(optional_array)
        object.__setattr__(self, "vertices_m", vertices_m)
        object.__setattr__(self, "tetrahedra", tetrahedra)
        object.__setattr__(self, "displacement_m", displacement_m)
        object.__setattr__(self, "von_mises_pa", von_mises_pa)
        object.__setattr__(self, "phase_degrees", phase_degrees)
        object.__setattr__(self, "element_strain", element_strain)
        object.__setattr__(self, "nodal_maximum_shear_pa", nodal_maximum_shear_pa)
        object.__setattr__(self, "nodal_factor_of_safety", nodal_factor_of_safety)


@dataclass(frozen=True)
class MeshConvergenceResult:
    """The relative medium-to-fine changes and resulting convergence decision."""

    medium: MeshMetrics
    fine: MeshMetrics
    stress_change_fraction: float
    displacement_change_fraction: float
    tolerance: float
    converged: bool

    def __post_init__(self) -> None:
        if not isinstance(self.medium, MeshMetrics) or not isinstance(
            self.fine, MeshMetrics
        ):
            raise ValueError("medium and fine must be MeshMetrics instances")
        for field_name in (
            "stress_change_fraction",
            "displacement_change_fraction",
            "tolerance",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{field_name} must be a finite non-negative number")
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{field_name} must be a finite non-negative number")
        if self.tolerance == 0.0:
            raise ValueError("tolerance must be positive")
        if not isinstance(self.converged, bool):
            raise ValueError("converged must be a boolean")
        expected_converged = (
            self.stress_change_fraction < self.tolerance
            and self.displacement_change_fraction < self.tolerance
        )
        if self.converged != expected_converged:
            raise ValueError("converged must match the supplied changes and tolerance")


@dataclass(frozen=True)
class Stage1FeaPackage:
    """A complete Stage 1 result set with report-ready metadata."""

    schema_version: str
    shaft: ComponentFieldResult
    gear: ComponentFieldResult
    drum: ComponentFieldResult
    solver_versions: Mapping[str, str] = field(default_factory=dict)
    assumptions: Mapping[str, Any] = field(default_factory=dict)
    convergence: Mapping[str, MeshConvergenceResult] = field(default_factory=dict)
    inputs: Mapping[str, Any] = field(default_factory=dict)
    analytical_values: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported FEA schema version: {self.schema_version!r}")
        for name, expected_phase_count in (("shaft", 1), ("gear", 1), ("drum", 12)):
            component = getattr(self, name)
            if not isinstance(component, ComponentFieldResult):
                raise ValueError(f"{name} must be a ComponentFieldResult")
            if component.displacement_m.shape[0] != expected_phase_count:
                raise ValueError(f"{name} must contain {expected_phase_count} phases")
        expected_drum_phases = tuple(float(index * 30) for index in range(12))
        if self.drum.phase_degrees != expected_drum_phases:
            raise ValueError("drum phase_degrees must run from 0 to 330 in 30 degree steps")

        solver_versions = _normalize_solver_versions(self.solver_versions)
        assumptions = _normalize_json_mapping(self.assumptions, "assumptions")
        convergence = _normalize_convergence(self.convergence)
        inputs = _normalize_json_mapping(self.inputs, "inputs")
        analytical_values = _normalize_json_mapping(
            self.analytical_values, "analytical_values"
        )
        object.__setattr__(self, "solver_versions", solver_versions)
        object.__setattr__(self, "assumptions", assumptions)
        object.__setattr__(self, "convergence", convergence)
        object.__setattr__(self, "inputs", inputs)
        object.__setattr__(self, "analytical_values", analytical_values)


def evaluate_mesh_convergence(
    medium: MeshMetrics, fine: MeshMetrics, tolerance: float = 0.10
) -> MeshConvergenceResult:
    """Compare medium and fine mesh metrics using relative changes from medium."""

    if not isinstance(medium, MeshMetrics) or not isinstance(fine, MeshMetrics):
        raise ValueError("medium and fine must be MeshMetrics instances")
    if not isinstance(tolerance, (int, float)) or isinstance(tolerance, bool):
        raise ValueError("tolerance must be a finite positive number")
    if not math.isfinite(tolerance) or tolerance <= 0.0:
        raise ValueError("tolerance must be a finite positive number")

    stress_change = _relative_change(
        medium.stress_95th_percentile_pa, fine.stress_95th_percentile_pa
    )
    displacement_change = _relative_change(
        medium.maximum_displacement_m, fine.maximum_displacement_m
    )
    return MeshConvergenceResult(
        medium=medium,
        fine=fine,
        stress_change_fraction=stress_change,
        displacement_change_fraction=displacement_change,
        tolerance=float(tolerance),
        converged=stress_change < tolerance and displacement_change < tolerance,
    )


def save_stage1_package(package: Stage1FeaPackage, directory: Path) -> Path:
    """Atomically write a validated package directory and return its path.

    Arrays are fully written to a sibling staging directory before the final
    directory is replaced.  An existing package is retained if replacement
    fails after its temporary backup has been made.
    """

    if not isinstance(package, Stage1FeaPackage):
        raise ValueError("package must be a Stage1FeaPackage")
    destination = Path(directory)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _recover_interrupted_replacement(destination)
    if destination.exists() and not destination.is_dir():
        raise ValueError("directory must name a directory, not a file")
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent)
    )
    try:
        _write_package_contents(package, staging)
        _replace_directory(staging, destination)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination


def load_stage1_package(directory: Path) -> Stage1FeaPackage:
    """Load and fully validate a versioned Stage 1 FEA package."""

    package_directory = Path(directory)
    _recover_interrupted_replacement(package_directory)
    return _load_stage1_package_contents(package_directory)


def load_stage1_package_read_only(directory: Path) -> Stage1FeaPackage:
    """Load and validate a package without recovering or mutating cache state."""

    return _load_stage1_package_contents(Path(directory))


def _load_stage1_package_contents(package_directory: Path) -> Stage1FeaPackage:
    summary_path = package_directory / "summary.json"
    if not summary_path.is_file():
        raise ValueError(f"Missing FEA package summary: {summary_path}")
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid FEA package summary: {error}") from error
    if not isinstance(summary, dict):
        raise ValueError("FEA package summary must be a JSON object")
    if summary.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported FEA schema version: {summary.get('schema_version')!r}"
        )
    if set(summary) != _SUMMARY_FIELDS:
        raise ValueError("FEA package summary has an invalid schema")

    components = _require_mapping(summary, "components")
    if set(components) != set(_COMPONENT_NAMES):
        raise ValueError("FEA package summary must describe shaft, gear, and drum")
    loaded_components = {
        name: _load_component(package_directory, name, components[name])
        for name in _COMPONENT_NAMES
    }
    solver_versions = _normalize_solver_versions(
        _require_mapping(summary, "solver_versions")
    )
    assumptions = _normalize_json_mapping(
        _require_mapping(summary, "assumptions"), "assumptions"
    )
    convergence = _load_convergence(_require_mapping(summary, "convergence"))
    inputs = _normalize_json_mapping(_require_mapping(summary, "inputs"), "inputs")
    analytical_values = _normalize_json_mapping(
        _require_mapping(summary, "analytical_values"), "analytical_values"
    )
    return Stage1FeaPackage(
        schema_version=SCHEMA_VERSION,
        shaft=loaded_components["shaft"],
        gear=loaded_components["gear"],
        drum=loaded_components["drum"],
        solver_versions=solver_versions,
        assumptions=assumptions,
        convergence=convergence,
        inputs=inputs,
        analytical_values=analytical_values,
    )


def _finite_float_array(value: Any, field_name: str) -> np.ndarray:
    try:
        array = np.array(value, dtype=np.float64, copy=True)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be a numeric array") from error
    if not np.isfinite(array).all():
        raise ValueError(f"{field_name} must contain only finite values")
    return array


def _optional_finite_float_array(value: Any, field_name: str) -> np.ndarray | None:
    if value is None:
        return None
    return _finite_float_array(value, field_name)


def _integer_array(value: Any, field_name: str) -> np.ndarray:
    array = np.asarray(value)
    if not np.issubdtype(array.dtype, np.integer) or np.issubdtype(
        array.dtype, np.bool_
    ):
        raise ValueError(f"{field_name} must be an integer array")
    return np.array(array, dtype=np.int64, copy=True)


def _freeze_array(array: np.ndarray) -> None:
    array.setflags(write=False)


def _relative_change(medium_value: float, fine_value: float) -> float:
    if medium_value == 0.0:
        # This finite sentinel remains JSON-safe and cannot pass a finite tolerance.
        return 0.0 if fine_value == 0.0 else float_info.max
    return abs(fine_value - medium_value) / abs(medium_value)


def _normalize_solver_versions(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("solver_versions must be a mapping")
    normalized: dict[str, str] = {}
    for key, version in value.items():
        if not isinstance(key, str) or not isinstance(version, str):
            raise ValueError("solver_versions must map strings to strings")
        normalized[key] = version
    return dict(sorted(normalized.items()))


def _normalize_json_mapping(value: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError(f"{field_name} keys must be strings")
        normalized[key] = _normalize_json_value(item, f"{field_name}.{key}")
    return dict(sorted(normalized.items()))


def _normalize_json_value(value: Any, field_name: str) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} must contain only finite numbers")
        return value
    if isinstance(value, list) or isinstance(value, tuple):
        return [_normalize_json_value(item, field_name) for item in value]
    if isinstance(value, Mapping):
        return _normalize_json_mapping(value, field_name)
    raise ValueError(f"{field_name} must be JSON-compatible")


def _normalize_convergence(
    value: Mapping[str, MeshConvergenceResult],
) -> dict[str, MeshConvergenceResult]:
    if not isinstance(value, Mapping):
        raise ValueError("convergence must be a mapping")
    normalized: dict[str, MeshConvergenceResult] = {}
    for name, result in value.items():
        if name not in _COMPONENT_NAMES:
            raise ValueError(f"convergence has unsupported component: {name}")
        if not isinstance(result, MeshConvergenceResult):
            raise ValueError("convergence values must be MeshConvergenceResult instances")
        normalized[name] = result
    return dict(sorted(normalized.items()))


def _write_package_contents(package: Stage1FeaPackage, directory: Path) -> None:
    components = {}
    for name in _COMPONENT_NAMES:
        component = getattr(package, name)
        filename = _COMPONENT_FILENAMES[name]
        _write_deterministic_npz(directory / filename, component)
        components[name] = _component_summary(component, filename)
    summary = {
        "analytical_values": package.analytical_values,
        "assumptions": package.assumptions,
        "components": components,
        "convergence": _convergence_summary(package.convergence),
        "inputs": package.inputs,
        "schema_version": package.schema_version,
        "solver_versions": package.solver_versions,
    }
    encoded_summary = json.dumps(
        summary, indent=2, sort_keys=True, allow_nan=False
    ) + "\n"
    (directory / "summary.json").write_text(encoded_summary, encoding="utf-8")


def _write_deterministic_npz(path: Path, component: ComponentFieldResult) -> None:
    arrays: list[tuple[str, np.ndarray]] = [
        ("vertices_m", component.vertices_m),
        ("tetrahedra", component.tetrahedra),
        ("displacement_m", component.displacement_m),
        ("von_mises_pa", component.von_mises_pa),
    ]
    for name in _OPTIONAL_ARRAY_NAMES:
        array = getattr(component, name)
        if array is not None:
            arrays.append((name, array))
    with zipfile.ZipFile(
        path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name, array in arrays:
            payload = BytesIO()
            np.save(payload, array, allow_pickle=False)
            entry = zipfile.ZipInfo(f"{name}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry._compresslevel = 9
            entry.create_system = 0
            entry.external_attr = 0
            archive.writestr(entry, payload.getvalue())


def _component_summary(component: ComponentFieldResult, filename: str) -> dict[str, Any]:
    displacement_magnitude = np.linalg.norm(component.displacement_m, axis=2)
    summary = {
        "element_count": int(component.tetrahedra.shape[0]),
        "file": filename,
        "maximum_displacement_m": float(np.max(displacement_magnitude)),
        "maximum_von_mises_pa": float(np.max(component.von_mises_pa)),
        "node_count": int(component.vertices_m.shape[0]),
        "phase_count": int(component.displacement_m.shape[0]),
        "phase_degrees": [float(value) for value in component.phase_degrees],
        "stress_95th_percentile_pa": float(np.percentile(component.von_mises_pa, 95)),
    }
    if component.nodal_maximum_shear_pa is not None:
        summary["maximum_shear_pa"] = float(np.max(component.nodal_maximum_shear_pa))
    if component.nodal_factor_of_safety is not None:
        summary["minimum_factor_of_safety"] = float(
            np.min(component.nodal_factor_of_safety)
        )
    return summary


def _convergence_summary(
    convergence: Mapping[str, MeshConvergenceResult],
) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for name in _COMPONENT_NAMES:
        result = convergence.get(name)
        if result is None:
            summary[name] = {"status": "not_evaluated"}
            continue
        summary[name] = {
            "displacement_change_fraction": float(result.displacement_change_fraction),
            "fine": _mesh_metrics_summary(result.fine),
            "medium": _mesh_metrics_summary(result.medium),
            "status": "converged" if result.converged else "unresolved_sensitivity",
            "stress_change_fraction": float(result.stress_change_fraction),
            "tolerance": float(result.tolerance),
        }
    return summary


def _mesh_metrics_summary(metrics: MeshMetrics) -> dict[str, Any]:
    return {
        "element_count": metrics.element_count,
        "maximum_displacement_m": float(metrics.maximum_displacement_m),
        "node_count": metrics.node_count,
        "stress_95th_percentile_pa": float(metrics.stress_95th_percentile_pa),
    }


def _replace_directory(staging: Path, destination: Path) -> None:
    backup: Path | None = None
    if destination.exists():
        backup = _backup_path(destination)
        if backup.exists():
            raise OSError(f"Unrecovered FEA package backup already exists: {backup}")
        os.replace(destination, backup)
    try:
        os.replace(staging, destination)
    except Exception:
        if backup is not None and backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    else:
        if backup is not None:
            shutil.rmtree(backup)


def _backup_path(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.backup"


def _recover_interrupted_replacement(destination: Path) -> None:
    """Resolve the two crash states around the final directory swap."""

    backup = _backup_path(destination)
    if not backup.exists():
        return
    if not destination.exists():
        os.replace(backup, destination)
        return

    try:
        _load_stage1_package_contents(destination)
    except (TypeError, ValueError):
        _remove_path(destination)
        os.replace(backup, destination)
    else:
        _remove_path(backup)


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path)


def _require_mapping(summary: Mapping[str, Any], field_name: str) -> Mapping[str, Any]:
    value = summary.get(field_name)
    if not isinstance(value, Mapping):
        raise ValueError(f"FEA package summary field {field_name!r} must be an object")
    return value


def _load_component(
    package_directory: Path, name: str, metadata_value: Any
) -> ComponentFieldResult:
    if not isinstance(metadata_value, Mapping):
        raise ValueError(f"FEA package component {name!r} metadata must be an object")
    metadata = metadata_value
    expected_filename = _COMPONENT_FILENAMES[name]
    if metadata.get("file") != expected_filename:
        raise ValueError(f"FEA package component {name!r} has an invalid array filename")
    array_path = package_directory / expected_filename
    if not array_path.is_file():
        raise ValueError(f"Missing FEA package array file: {array_path}")
    phase_degrees = metadata.get("phase_degrees")
    if not isinstance(phase_degrees, list):
        raise ValueError(
            f"FEA package component {name!r} has invalid phase_degrees"
        )
    if any(
        not isinstance(value, (int, float)) or isinstance(value, bool)
        for value in phase_degrees
    ):
        raise ValueError(
            f"FEA package component {name!r} has invalid phase_degrees elements"
        )
    try:
        with np.load(array_path, allow_pickle=False) as arrays:
            array_names = set(arrays.files)
            allowed_array_names = set(_BASE_ARRAY_NAMES) | set(_OPTIONAL_ARRAY_NAMES)
            if not set(_BASE_ARRAY_NAMES).issubset(array_names) or not array_names.issubset(
                allowed_array_names
            ):
                raise ValueError(f"FEA package component {name!r} has invalid array fields")
            component = ComponentFieldResult(
                vertices_m=arrays["vertices_m"],
                tetrahedra=arrays["tetrahedra"],
                displacement_m=arrays["displacement_m"],
                von_mises_pa=arrays["von_mises_pa"],
                phase_degrees=tuple(phase_degrees),
                element_strain=(
                    arrays["element_strain"] if "element_strain" in array_names else None
                ),
                nodal_maximum_shear_pa=(
                    arrays["nodal_maximum_shear_pa"]
                    if "nodal_maximum_shear_pa" in array_names
                    else None
                ),
                nodal_factor_of_safety=(
                    arrays["nodal_factor_of_safety"]
                    if "nodal_factor_of_safety" in array_names
                    else None
                ),
            )
    except (OSError, TypeError, ValueError, zipfile.BadZipFile) as error:
        if isinstance(error, ValueError) and str(error).startswith("FEA package"):
            raise
        raise ValueError(f"Invalid FEA package component {name!r}: {error}") from error
    _validate_component_metadata(name, metadata, component)
    return component


def _validate_component_metadata(
    name: str, metadata: Mapping[str, Any], component: ComponentFieldResult
) -> None:
    expected = _component_summary(component, _COMPONENT_FILENAMES[name])
    if set(metadata) != set(expected):
        raise ValueError(f"FEA package component {name!r} metadata has an invalid schema")
    for field_name in ("node_count", "element_count", "phase_count"):
        value = metadata[field_name]
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(
                f"FEA package component {name!r} has invalid {field_name}"
            )
        if value != expected[field_name]:
            raise ValueError(f"FEA package component {name!r} has inconsistent {field_name}")
    if metadata["phase_degrees"] != expected["phase_degrees"]:
        raise ValueError(f"FEA package component {name!r} has inconsistent phase_degrees")
    numeric_fields = set(expected) - {
        "element_count",
        "file",
        "node_count",
        "phase_count",
        "phase_degrees",
    }
    for field_name in numeric_fields:
        value = metadata[field_name]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"FEA package component {name!r} has invalid {field_name}")
        if not math.isfinite(value) or value != expected[field_name]:
            raise ValueError(f"FEA package component {name!r} has inconsistent {field_name}")


def _load_convergence(value: Mapping[str, Any]) -> dict[str, MeshConvergenceResult]:
    if set(value) != set(_COMPONENT_NAMES):
        raise ValueError("FEA package convergence must describe shaft, gear, and drum")
    loaded: dict[str, MeshConvergenceResult] = {}
    for name in _COMPONENT_NAMES:
        entry = value[name]
        if not isinstance(entry, Mapping) or not isinstance(entry.get("status"), str):
            raise ValueError(f"FEA package convergence for {name!r} has an invalid schema")
        if entry["status"] == "not_evaluated":
            if set(entry) != {"status"}:
                raise ValueError(f"FEA package convergence for {name!r} has an invalid schema")
            continue
        if entry["status"] not in {"converged", "unresolved_sensitivity"}:
            raise ValueError(f"FEA package convergence for {name!r} has an invalid status")
        required = {
            "status",
            "medium",
            "fine",
            "stress_change_fraction",
            "displacement_change_fraction",
            "tolerance",
        }
        if set(entry) != required:
            raise ValueError(f"FEA package convergence for {name!r} has an invalid schema")
        medium = _load_mesh_metrics(entry["medium"], f"convergence.{name}.medium")
        fine = _load_mesh_metrics(entry["fine"], f"convergence.{name}.fine")
        stress_change = _finite_nonnegative_number(
            entry["stress_change_fraction"], f"convergence.{name}.stress_change_fraction"
        )
        displacement_change = _finite_nonnegative_number(
            entry["displacement_change_fraction"],
            f"convergence.{name}.displacement_change_fraction",
        )
        tolerance = _finite_nonnegative_number(entry["tolerance"], f"convergence.{name}.tolerance")
        result = MeshConvergenceResult(
            medium=medium,
            fine=fine,
            stress_change_fraction=stress_change,
            displacement_change_fraction=displacement_change,
            tolerance=tolerance,
            converged=entry["status"] == "converged",
        )
        expected = evaluate_mesh_convergence(medium, fine, tolerance)
        if (
            result.stress_change_fraction != expected.stress_change_fraction
            or result.displacement_change_fraction
            != expected.displacement_change_fraction
            or result.converged != expected.converged
        ):
            raise ValueError(f"FEA package convergence for {name!r} is inconsistent")
        loaded[name] = result
    return loaded


def _load_mesh_metrics(value: Any, field_name: str) -> MeshMetrics:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    required = {
        "stress_95th_percentile_pa",
        "maximum_displacement_m",
        "node_count",
        "element_count",
    }
    if set(value) != required:
        raise ValueError(f"{field_name} has an invalid schema")
    return MeshMetrics(
        stress_95th_percentile_pa=_finite_nonnegative_number(
            value["stress_95th_percentile_pa"], f"{field_name}.stress_95th_percentile_pa"
        ),
        maximum_displacement_m=_finite_nonnegative_number(
            value["maximum_displacement_m"], f"{field_name}.maximum_displacement_m"
        ),
        node_count=_nonnegative_integer(value["node_count"], f"{field_name}.node_count"),
        element_count=_nonnegative_integer(
            value["element_count"], f"{field_name}.element_count"
        ),
    )


def _finite_nonnegative_number(value: Any, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be a finite non-negative number")
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{field_name} must be a finite non-negative number")
    return float(value)


def _nonnegative_integer(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value
