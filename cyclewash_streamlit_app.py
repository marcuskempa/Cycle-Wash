"""Streamlit Gear Builder for the CycleWash bicycle drivetrain."""

from __future__ import annotations

import math
import sys
from pathlib import Path


CURRENT_DIR = Path(__file__).resolve().parent
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

from cyclewash_cad_calculator import (
    DEFAULT_CHAIN_PITCH_MM,
    DEFAULT_COG_THICKNESS_MM,
    DEFAULT_DRUM_RADIUS_M,
    DEFAULT_FRONT_TEETH,
    DEFAULT_OUTPUT_PATH,
    DEFAULT_PEDAL_RPM,
    DEFAULT_REFERENCE_REAR_TEETH,
    DEFAULT_SHAFT_LENGTH_MM,
    DEFAULT_SHAFT_RADIUS_MM,
    DEFAULT_TARGET_DRUM_RPM,
    DEFAULT_TARGET_VELOCITY_MAX,
    DEFAULT_TARGET_VELOCITY_MIN,
    calculate_cyclewash_drive,
    export_cyclewash_stl,
    format_engineering_summary,
)
from cyclewash_plotly_geometry import (
    GearMeshData,
    build_gear_mesh_data,
    build_reference_gear_mesh_data,
)
from cyclewash_structural_visualizer import StlPartSpec, load_stl_part


REFERENCE_GEAR_PATH = CURRENT_DIR / "gear.stl"


def _build_shaft_surface(
    shaft_radius_mm: float,
    shaft_length_mm: float,
    angular_steps: int = 48,
) -> go.Surface:
    """Build a translucent shaft cylinder for the Plotly scene."""
    theta_values = [2.0 * math.pi * index / angular_steps for index in range(angular_steps + 1)]
    z_values = [-shaft_length_mm / 2.0, shaft_length_mm / 2.0]
    x_grid: list[list[float]] = []
    y_grid: list[list[float]] = []
    z_grid: list[list[float]] = []

    for z_value in z_values:
        x_grid.append([shaft_radius_mm * math.cos(theta) for theta in theta_values])
        y_grid.append([shaft_radius_mm * math.sin(theta) for theta in theta_values])
        z_grid.append([z_value for _ in theta_values])

    return go.Surface(
        x=x_grid,
        y=y_grid,
        z=z_grid,
        colorscale=[[0, "#8a8f98"], [1, "#c8ccd2"]],
        opacity=0.55,
        showscale=False,
        name="Shaft",
        hoverinfo="skip",
    )


def build_gear_figure(
    tooth_count: int,
    chain_pitch_mm: float,
    shaft_radius_mm: float,
    shaft_length_mm: float,
    cog_thickness_mm: float,
    mesh_data: GearMeshData | None = None,
) -> go.Figure:
    """Create the Plotly 3D gear viewer figure."""
    mesh = mesh_data or build_gear_mesh_data(
        tooth_count, chain_pitch_mm, shaft_radius_mm, cog_thickness_mm
    )

    figure = go.Figure()
    figure.add_trace(
        go.Mesh3d(
            x=mesh.x,
            y=mesh.y,
            z=mesh.z,
            i=mesh.i,
            j=mesh.j,
            k=mesh.k,
            color="#3b82f6",
            opacity=0.96,
            flatshading=True,
            lighting={"ambient": 0.42, "diffuse": 0.7, "specular": 0.25, "roughness": 0.55},
            name=f"{tooth_count}-tooth rear cog",
            hovertemplate=(
                "x: %{x:.1f} mm<br>"
                "y: %{y:.1f} mm<br>"
                "z: %{z:.1f} mm"
                "<extra></extra>"
            ),
        )
    )
    figure.add_trace(_build_shaft_surface(shaft_radius_mm, shaft_length_mm))

    axis_limit_mm = max(mesh.outer_radius_mm * 1.2, shaft_length_mm * 0.65)
    axis_style = {
        "backgroundcolor": "#0e1117",
        "gridcolor": "#263244",
        "zerolinecolor": "#405067",
        "color": "#cbd5e1",
        "showbackground": True,
    }
    figure.update_layout(
        margin={"l": 0, "r": 0, "t": 12, "b": 0},
        paper_bgcolor="#0e1117",
        plot_bgcolor="#0e1117",
        font={"color": "#e5e7eb"},
        scene={
            "bgcolor": "#0e1117",
            "aspectmode": "data",
            "xaxis": {
                **axis_style,
                "title": "X (mm)",
                "range": [-axis_limit_mm, axis_limit_mm],
            },
            "yaxis": {
                **axis_style,
                "title": "Y (mm)",
                "range": [-axis_limit_mm, axis_limit_mm],
            },
            "zaxis": {
                **axis_style,
                "title": "Z (mm)",
                "range": [-shaft_length_mm * 0.6, shaft_length_mm * 0.6],
            },
            "camera": {"eye": {"x": 1.35, "y": 1.45, "z": 0.9}},
        },
        height=640,
        showlegend=False,
    )
    return figure


def _load_reference_gear_mesh(
    chain_pitch_mm: float,
    shaft_radius_mm: float,
) -> GearMeshData | None:
    """Load and normalize the fixed reference STL, returning None on failure."""
    if not REFERENCE_GEAR_PATH.is_file():
        return None
    try:
        part = load_stl_part(
            StlPartSpec(
                name="Existing 32T STL",
                source=REFERENCE_GEAR_PATH,
                component_kind="rotational",
            )
        )
        return build_reference_gear_mesh_data(
            vertices=part.local_vertices,
            faces=part.faces,
            tooth_count=DEFAULT_REFERENCE_REAR_TEETH,
            chain_pitch_mm=chain_pitch_mm,
            shaft_radius_mm=shaft_radius_mm,
        )
    except (OSError, ValueError):
        return None


def _resolve_gear_mesh(
    result,
    reference_mesh: GearMeshData | None,
    shaft_radius_mm: float,
    cog_thickness_mm: float,
) -> tuple[GearMeshData, str, bool]:
    """Return the already-validated reference mesh or calculated geometry."""
    if result.gear_source == "reference_stl" and reference_mesh is not None:
        return reference_mesh, "Existing 32T STL", True

    mesh = build_gear_mesh_data(
        tooth_count=result.practical_rear_teeth,
        chain_pitch_mm=result.chain_pitch_mm,
        shaft_radius_mm=shaft_radius_mm,
        cog_thickness_mm=cog_thickness_mm,
    )
    return mesh, "Calculated sprocket", False


def _velocity_message(result) -> None:
    """Render velocity status with Streamlit's native status components."""
    if result.velocity_status == "within target":
        st.success(
            f"Fluid velocity is within target range "
            f"({result.target_velocity_min:.2f} - {result.target_velocity_max:.2f} m/s)."
        )
    elif result.velocity_status == "below target":
        st.warning("Fluid velocity is below the target range.")
    else:
        st.error("Fluid velocity is above the target range.")


def main() -> None:
    """Run the Streamlit GUI."""
    st.set_page_config(page_title="CycleWash Gear Builder", layout="wide")
    st.title("CycleWash Gear Builder")

    with st.sidebar:
        st.header("Drivetrain")
        pedal_rpm = st.number_input(
            "Target pedaling cadence (RPM)",
            min_value=1.0,
            max_value=180.0,
            value=float(DEFAULT_PEDAL_RPM),
            step=1.0,
            help=(
                "Sustainable bicycle crank speed used as the drivetrain design input. "
                "About 60 RPM is a common steady cadence."
            ),
        )
        target_drum_rpm = st.number_input(
            "Target drum speed (RPM)",
            min_value=1.0,
            max_value=300.0,
            value=float(DEFAULT_TARGET_DRUM_RPM),
            step=1.0,
            help=(
                "Desired inner-drum speed in revolutions per minute. "
                "CycleWash commonly targets about 60 RPM."
            ),
        )
        front_teeth = st.number_input(
            "Front chainring teeth",
            min_value=3,
            max_value=80,
            value=int(DEFAULT_FRONT_TEETH),
            step=1,
            help=(
                "Number of teeth on the bicycle crank chainring. More front teeth "
                "increase drum speed but reduce available drum torque."
            ),
        )
        drum_radius_m = st.number_input(
            "Inner drum radius (m)",
            min_value=0.150,
            max_value=0.400,
            value=float(DEFAULT_DRUM_RADIUS_M),
            step=0.010,
            format="%.3f",
            help=(
                "Distance from the drum axis to its inner wall; used to calculate "
                "drum-edge fluid velocity."
            ),
        )
        chain_pitch_mm = st.number_input(
            "Chain pitch (mm)",
            min_value=1.0,
            max_value=50.0,
            value=float(DEFAULT_CHAIN_PITCH_MM),
            step=0.1,
            help=(
                "Distance between adjacent chain pins. Standard bicycle chain pitch "
                "is 12.7 mm (1/2 inch)."
            ),
        )

        st.header("Target")
        target_velocity_min = st.number_input(
            "Minimum fluid velocity (m/s)",
            min_value=0.1,
            max_value=10.0,
            value=float(DEFAULT_TARGET_VELOCITY_MIN),
            step=0.1,
            help="Lower acceptable drum-edge fluid velocity for the washing target.",
        )
        target_velocity_max = st.number_input(
            "Maximum fluid velocity (m/s)",
            min_value=0.1,
            max_value=10.0,
            value=float(DEFAULT_TARGET_VELOCITY_MAX),
            step=0.1,
            help="Upper acceptable drum-edge fluid velocity for the washing target.",
        )

        st.header("Geometry")
        shaft_radius_mm = st.number_input(
            "Shaft radius (mm)",
            min_value=1.0,
            max_value=50.0,
            value=float(DEFAULT_SHAFT_RADIUS_MM),
            step=0.5,
            help=(
                "Radius of the solid drum drive shaft. A 12.5 mm radius represents "
                "a 25 mm diameter shaft."
            ),
        )
        shaft_length_mm = st.number_input(
            "Shaft length (mm)",
            min_value=10.0,
            max_value=500.0,
            value=float(DEFAULT_SHAFT_LENGTH_MM),
            step=5.0,
            help="Modeled shaft length used by the viewer and generated CAD assembly.",
        )
        cog_thickness_mm = st.number_input(
            "Cog thickness (mm)",
            min_value=1.0,
            max_value=50.0,
            value=float(DEFAULT_COG_THICKNESS_MM),
            step=0.5,
            help=(
                "Axial thickness of the rear sprocket. About 3 mm matches a typical "
                "single-speed bicycle sprocket."
            ),
        )

    try:
        reference_mesh = _load_reference_gear_mesh(
            chain_pitch_mm=chain_pitch_mm,
            shaft_radius_mm=shaft_radius_mm,
        )
        result = calculate_cyclewash_drive(
            pedal_rpm=pedal_rpm,
            target_drum_rpm=target_drum_rpm,
            front_teeth=int(front_teeth),
            drum_radius_m=drum_radius_m,
            chain_pitch_mm=chain_pitch_mm,
            target_velocity_min=target_velocity_min,
            target_velocity_max=target_velocity_max,
            prefer_reference_gear=reference_mesh is not None,
        )
        gear_mesh, gear_source_label, using_reference_mesh = _resolve_gear_mesh(
            result=result,
            reference_mesh=reference_mesh,
            shaft_radius_mm=shaft_radius_mm,
            cog_thickness_mm=cog_thickness_mm,
        )
        figure = build_gear_figure(
            tooth_count=result.practical_rear_teeth,
            chain_pitch_mm=result.chain_pitch_mm,
            shaft_radius_mm=shaft_radius_mm,
            shaft_length_mm=shaft_length_mm,
            cog_thickness_mm=cog_thickness_mm,
            mesh_data=gear_mesh,
        )
    except ValueError as error:
        st.error(str(error))
        return

    metric_columns = st.columns(6)
    metric_columns[0].metric("Selected gear", f"{result.practical_rear_teeth} teeth")
    metric_columns[1].metric("Actual drum", f"{result.actual_drum_rpm:.2f} RPM")
    metric_columns[2].metric("Fluid velocity", f"{result.fluid_velocity_m_s:.3f} m/s")
    metric_columns[3].metric("Actual ratio", f"{result.actual_ratio:.4f}:1")
    metric_columns[4].metric(
        "Speed deviation", f"{result.drum_speed_error_percent:+.2f}%"
    )
    metric_columns[5].metric(
        "Target cadence", f"{result.required_pedal_rpm_for_target:.2f} RPM"
    )

    _velocity_message(result)

    left_column, right_column = st.columns([1.45, 1.0], gap="large")
    with left_column:
        st.caption(f"Geometry source: {gear_source_label}")
        st.plotly_chart(figure, width="stretch")

    with right_column:
        st.subheader("Engineering Summary")
        st.code(format_engineering_summary(result), language="text")

        if using_reference_mesh:
            st.download_button(
                "Download selected STL",
                data=REFERENCE_GEAR_PATH.read_bytes(),
                file_name="cyclewash_existing_32t_gear.stl",
                mime="model/stl",
                type="primary",
            )
        else:
            output_path = CURRENT_DIR / DEFAULT_OUTPUT_PATH.name
        if not using_reference_mesh and st.button("Export calculated STL", type="primary"):
            try:
                exported_path = export_cyclewash_stl(
                    result=result,
                    output_path=output_path,
                    shaft_radius_mm=shaft_radius_mm,
                    shaft_length_mm=shaft_length_mm,
                    cog_thickness_mm=cog_thickness_mm,
                )
            except ImportError as error:
                st.error(str(error))
            except ValueError as error:
                st.error(str(error))
            else:
                st.success(f"STL exported to {exported_path}")


if __name__ == "__main__":
    main()
