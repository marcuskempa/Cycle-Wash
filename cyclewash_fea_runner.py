"""Run the optional CycleWash FEA solver through an isolated subprocess.

The application environment stays dependency-light: solver inputs cross the
boundary as canonical JSON and solver results return as a validated Stage 1
package.  A failed solver run cannot replace a previously published package.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from queue import Empty, Queue
import shutil
import subprocess
import tempfile
from threading import Thread
import time
from typing import Any, Callable, Sequence
from uuid import uuid4

import cyclewash_engineering_model as engineering
import cyclewash_fea_results as fea_results


_SOLVER_PYTHON = Path("work") / ".fea-venv" / "Scripts" / "python.exe"
_SETUP_COMMAND = "setup_cyclewash_fea.bat [C:\\path\\to\\python3.12.exe]"
DEFAULT_FEA_TIMEOUT_SECONDS = 3600.0
_VERSION_PROBE = (
    "import json, sys; import gmsh, sfepy, meshio; "
    "print(json.dumps({'python': '.'.join(map(str, sys.version_info[:3])), "
    "'gmsh': gmsh.__version__, 'sfepy': sfepy.__version__, "
    "'meshio': meshio.__version__}, sort_keys=True))"
)


class FeaRunnerError(RuntimeError):
    """Raised when the isolated FEA solver cannot produce a valid package."""


@dataclass(frozen=True)
class FeaSolverStatus:
    """Availability and version information for the optional FEA environment."""

    available: bool
    python_path: Path | None
    versions: dict[str, str]
    message: str


def detect_fea_solver(project_root: Path) -> FeaSolverStatus:
    """Inspect the isolated solver environment without importing its packages."""

    root = Path(project_root)
    python_path = root / _SOLVER_PYTHON
    if not python_path.is_file():
        return FeaSolverStatus(
            available=False,
            python_path=python_path,
            versions={},
            message=(
                "Stage 1 FEA solver is not installed. Run "
                f"{_SETUP_COMMAND} from the project root with Python 3.12."
            ),
        )

    try:
        completed = subprocess.run(
            [str(python_path), "-c", _VERSION_PROBE],
            capture_output=True,
            check=False,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return FeaSolverStatus(
            available=False,
            python_path=python_path,
            versions={},
            message=(
                "Stage 1 FEA solver could not be verified "
                f"({error}). Re-run {_SETUP_COMMAND}."
            ),
        )

    if completed.returncode != 0:
        detail = _subprocess_detail(completed)
        return FeaSolverStatus(
            available=False,
            python_path=python_path,
            versions={},
            message=(
                "Stage 1 FEA solver dependencies are unavailable or broken. "
                f"Re-run {_SETUP_COMMAND}. Details: {detail}"
            ),
        )

    try:
        versions = _parse_version_probe(completed.stdout)
    except ValueError as error:
        return FeaSolverStatus(
            available=False,
            python_path=python_path,
            versions={},
            message=(
                "Stage 1 FEA solver verification returned invalid version data "
                f"({error}). Re-run {_SETUP_COMMAND}."
            ),
        )

    if not versions["python"].startswith("3.12."):
        return FeaSolverStatus(
            available=False,
            python_path=python_path,
            versions=versions,
            message=(
                "Stage 1 FEA solver must use Python 3.12; found "
                f"Python {versions['python']}. Re-run {_SETUP_COMMAND}."
            ),
        )

    return FeaSolverStatus(
        available=True,
        python_path=python_path,
        versions=versions,
        message="Stage 1 FEA solver is available.",
    )


def canonical_input_json(inputs: engineering.EngineeringInputs) -> str:
    """Serialize immutable engineering inputs as deterministic, strict JSON."""

    if not isinstance(inputs, engineering.EngineeringInputs):
        raise FeaRunnerError(
            "Invalid EngineeringInputs: expected an EngineeringInputs instance"
        )
    try:
        engineering.calculate_engineering_loads(inputs)
    except (TypeError, ValueError) as error:
        raise FeaRunnerError(f"Invalid EngineeringInputs: {error}") from error
    try:
        return json.dumps(
            inputs.to_dict(),
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise FeaRunnerError(
            "Invalid EngineeringInputs: values must be JSON-compatible"
        ) from error


def solver_request_hash(
    inputs: engineering.EngineeringInputs, mesh_levels: Sequence[str]
) -> str:
    """Return the stable SHA-256 key for a solver input and mesh request."""

    canonical_input_json(inputs)
    try:
        return engineering.canonical_request_identity(inputs, mesh_levels)[
            "request_sha256"
        ]
    except (TypeError, ValueError) as error:
        raise FeaRunnerError(f"Invalid solver request: {error}") from error


def normalize_mesh_levels(mesh_levels: Sequence[str]) -> tuple[str, ...]:
    """Public dependency-light mesh request normalization."""

    return engineering.normalize_mesh_levels(mesh_levels)


def package_matches_request(
    package: fea_results.Stage1FeaPackage,
    inputs: engineering.EngineeringInputs,
    mesh_levels: Sequence[str],
) -> bool:
    """Return whether a loaded package exactly matches inputs and mesh request."""

    if not isinstance(package, fea_results.Stage1FeaPackage):
        return False
    try:
        expected_identity = engineering.canonical_request_identity(inputs, mesh_levels)
    except (TypeError, ValueError):
        return False
    return (
        package.inputs == inputs.to_dict()
        and package.assumptions.get("request_identity") == expected_identity
    )


def run_fea_subprocess(
    inputs: engineering.EngineeringInputs,
    output_dir: Path,
    mesh_levels: Sequence[str],
    *,
    project_root: Path | None = None,
    solver_script: Path | None = None,
    timeout_seconds: float = DEFAULT_FEA_TIMEOUT_SECONDS,
    progress_callback: Callable[[float, str], None] | None = None,
) -> fea_results.Stage1FeaPackage:
    """Solve in the isolated environment and atomically publish a valid package.

    ``output_dir`` is the FEA result root; a package is published below it using
    the deterministic request SHA-256.  Optional keyword arguments exist for
    controlled integration tests and do not change the public three-argument
    call contract.
    """

    levels = normalize_mesh_levels(mesh_levels)
    timeout = _normalize_timeout(timeout_seconds)
    input_json = canonical_input_json(inputs)
    request_hash = engineering.canonical_request_identity(inputs, levels)["request_sha256"]
    root = Path(project_root) if project_root is not None else _default_project_root()
    result_root = Path(output_dir)
    status = detect_fea_solver(root)
    if not status.available or status.python_path is None:
        raise FeaRunnerError(status.message)

    script = Path(solver_script) if solver_script is not None else root / "outputs" / "cyclewash_fea_solver.py"
    if not script.is_file():
        raise FeaRunnerError(
            f"Stage 1 FEA solver script is missing: {script}. "
            "Install or restore the solver before running FEA."
        )

    run_root = root / "work" / "fea-runs"
    run_root.mkdir(parents=True, exist_ok=True)
    run_directory = Path(tempfile.mkdtemp(prefix=f"{uuid4().hex}-", dir=run_root))
    input_path = run_directory / "engineering-inputs.json"
    staged_package = run_directory / "package"

    try:
        input_path.write_text(input_json, encoding="utf-8")
        command = [
            str(status.python_path),
            str(script),
            "--input",
            str(input_path),
            "--output",
            str(staged_package),
        ]
        for level in levels:
            command.extend(("--mesh-level", level))

        try:
            completed = _run_process_streaming(
                command,
                timeout,
                progress_callback,
            )
        except subprocess.TimeoutExpired as error:
            raise FeaRunnerError(
                f"Stage 1 FEA solver timed out after {timeout:g} seconds. "
                "Increase timeout_seconds for finer meshes or retry with a coarser mesh."
            ) from error
        except OSError as error:
            raise FeaRunnerError(f"Could not start Stage 1 FEA solver: {error}") from error

        if completed.returncode != 0:
            raise FeaRunnerError(
                "Stage 1 FEA solver failed with exit code "
                f"{completed.returncode}. {_subprocess_detail(completed)}"
            )

        try:
            package = fea_results.load_stage1_package(staged_package)
        except (OSError, ValueError) as error:
            raise FeaRunnerError(
                "Stage 1 FEA solver completed but produced an invalid FEA package: "
                f"{error}"
            ) from error
        if not package_matches_request(package, inputs, levels):
            raise FeaRunnerError(
                "Stage 1 FEA solver produced a package whose request identity does not match current inputs and mesh levels."
            )

        destination = result_root / request_hash
        try:
            fea_results.save_stage1_package(package, destination)
        except (OSError, ValueError) as error:
            raise FeaRunnerError(
                f"Could not atomically publish FEA package to {destination}: {error}"
            ) from error
        return package
    finally:
        shutil.rmtree(run_directory, ignore_errors=True)


def _parse_progress_line(line: str) -> tuple[float, str] | None:
    parts = line.strip().split(maxsplit=2)
    if len(parts) != 3 or parts[0] != "PROGRESS":
        return None
    try:
        fraction = float(parts[1])
    except ValueError:
        return None
    message = parts[2].strip()
    if not math.isfinite(fraction) or not 0.0 <= fraction <= 1.0 or not message:
        return None
    return fraction, message


def _run_process_streaming(
    command: list[str],
    timeout_seconds: float,
    progress_callback: Callable[[float, str], None] | None,
) -> subprocess.CompletedProcess[str]:
    """Capture both child streams while emitting valid progress lines live."""

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        raise OSError("Could not open solver output streams")

    events: Queue[tuple[str, str | None]] = Queue()

    def read_stream(name: str, stream: Any) -> None:
        try:
            for line in iter(stream.readline, ""):
                events.put((name, line))
        finally:
            stream.close()
            events.put((name, None))

    threads = [
        Thread(target=read_stream, args=("stdout", process.stdout), daemon=True),
        Thread(target=read_stream, args=("stderr", process.stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    closed_streams = 0
    started = time.monotonic()
    try:
        while closed_streams < 2:
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0.0:
                raise subprocess.TimeoutExpired(command, timeout_seconds)
            try:
                stream_name, line = events.get(timeout=min(0.05, remaining))
            except Empty:
                continue
            if line is None:
                closed_streams += 1
                continue
            if stream_name == "stdout":
                stdout_lines.append(line)
                progress = _parse_progress_line(line)
                if progress is not None and progress_callback is not None:
                    progress_callback(*progress)
            else:
                stderr_lines.append(line)
        returncode = process.wait(timeout=max(0.01, timeout_seconds - (time.monotonic() - started)))
    except Exception:
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    finally:
        for thread in threads:
            thread.join(timeout=0.2)

    return subprocess.CompletedProcess(
        command,
        returncode,
        stdout="".join(stdout_lines),
        stderr="".join(stderr_lines),
    )


def _default_project_root() -> Path:
    return Path(__file__).resolve().parent


def _normalize_mesh_levels(mesh_levels: Sequence[str]) -> tuple[str, ...]:
    return normalize_mesh_levels(mesh_levels)


def _normalize_timeout(timeout_seconds: float) -> float:
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0.0
    ):
        raise FeaRunnerError("timeout_seconds must be a finite positive number")
    return float(timeout_seconds)


def _solver_request_hash_from_json(
    input_json: str, mesh_levels: Sequence[str]
) -> str:
    request = {
        "engineering_inputs": json.loads(input_json),
        "mesh_levels": tuple(mesh_levels),
    }
    encoded = json.dumps(
        request, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_version_probe(stdout: str) -> dict[str, str]:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise ValueError("probe produced no JSON output")
    try:
        parsed = json.loads(lines[-1])
    except json.JSONDecodeError as error:
        raise ValueError("probe output was not JSON") from error
    if not isinstance(parsed, dict):
        raise ValueError("probe JSON was not an object")
    required = ("python", "gmsh", "sfepy", "meshio")
    if any(not isinstance(parsed.get(name), str) or not parsed[name] for name in required):
        raise ValueError("probe JSON omitted a required version")
    return {name: parsed[name] for name in required}


def _subprocess_detail(completed: subprocess.CompletedProcess[str]) -> str:
    stderr = completed.stderr.strip()
    stdout = completed.stdout.strip()
    if stderr:
        return f"stderr: {stderr}"
    if stdout:
        return f"stdout: {stdout}"
    return "The solver produced no diagnostic output."
