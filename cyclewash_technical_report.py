"""Immutable shared dataset for CycleWash technical-evaluation reports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np

from cyclewash_cad_calculator import DriveResult, calculate_cyclewash_drive
from cyclewash_dimensions import (
    DRUM_DEPTH_M,
    DRUM_EFFECTIVE_RADIUS_M,
    GEAR_PITCH_RADIUS_M,
    GEAR_TOOTH_COUNT,
    SHAFT_DIAMETER_M,
    SHAFT_LENGTH_M,
)
from cyclewash_fea_results import Stage1FeaPackage, load_stage1_package_read_only
from cyclewash_fea_runner import package_matches_request, solver_request_hash
from cyclewash_scenarios import SCENARIOS, OperatingScenario, ScenarioResults, calculate_scenario, scenario_by_name


CANONICAL_FEA_MESH_LEVELS: Final[tuple[str, ...]] = ("coarse",)
ANALYTICAL_PROVENANCE: Final[str] = "Analytical load estimate"
SOLVED_FEA_PROVENANCE: Final[str] = "Solved Stage 1 FEA"
CORE_FORMULA_IDS: Final[tuple[str, ...]] = (
    "drivetrain_speed_ratio",
    "angular_speed_and_edge_velocity",
    "unbalanced_wet_laundry_load",
    "combined_stress_and_factor_of_safety",
)
LIMITATIONS_NOTE: Final[str] = (
    "These results are simplified analytical estimates for an introductory design study, "
    "not validated structural FEA or CFD. The model lacks the detailed materials, boundary "
    "conditions, contacts, turbulence, and mesh refinement required for engineering validation."
)


@dataclass(frozen=True)
class SymbolDefinition:
    """One variable definition shown beside a report equation."""

    symbol: str
    meaning: str
    unit: str
    source: str


@dataclass(frozen=True)
class FormulaDefinition:
    """One explanatory equation with accessible offline display content."""

    identifier: str
    title: str
    latex: str
    html: str
    evaluated: str
    symbols: tuple[SymbolDefinition, ...]
    explanation: str


@dataclass(frozen=True)
class FeaComponentSummary:
    """Report-ready extrema and mesh counts for one cached FEA component."""

    name: str
    maximum_von_mises_pa: float
    maximum_displacement_m: float
    minimum_factor_of_safety: float | None
    node_count: int
    element_count: int


@dataclass(frozen=True)
class ScenarioReport:
    """Calculated scenario values and their structural-result provenance."""

    scenario: OperatingScenario
    results: ScenarioResults
    provenance: str
    fea_provenance: str | None
    fea_package: Stage1FeaPackage | None
    fea_components: tuple[FeaComponentSummary, ...]
    fea_summary: tuple[str, ...]


@dataclass(frozen=True)
class ReportDocument:
    """Single immutable source of engineering values for every future exporter."""

    project_dimensions: tuple[SymbolDefinition, ...]
    drivetrain: DriveResult
    scenario_reports: tuple[ScenarioReport, ...]
    selected_report: ScenarioReport
    formulas: tuple[FormulaDefinition, ...]
    provenance: tuple[str, ...]
    assumptions: tuple[str, ...]
    limitations: tuple[str, ...]
    engineering_interpretation: str
    conclusion: str
    units_note: str


def core_formulas(document: ReportDocument) -> tuple[FormulaDefinition, ...]:
    """Return the concise formula subset used by the introductory page."""

    by_identifier = {formula.identifier: formula for formula in document.formulas}
    return tuple(by_identifier[identifier] for identifier in CORE_FORMULA_IDS)


def build_report_document(
    selected_name: str, fea_root: Path | str | None = None
) -> ReportDocument:
    """Build all report content without solving or waiting for FEA."""

    selected_scenario = scenario_by_name(selected_name)
    scenario_reports = tuple(
        _build_scenario_report(
            scenario,
            fea_root,
            include_fea=scenario == selected_scenario,
        )
        for scenario in SCENARIOS
    )
    selected_report = next(
        report for report in scenario_reports if report.scenario == selected_scenario
    )
    drivetrain = calculate_cyclewash_drive(
        target_drum_rpm=selected_scenario.speed_rpm,
        drum_radius_m=DRUM_EFFECTIVE_RADIUS_M,
    )
    return ReportDocument(
        project_dimensions=_project_dimensions(),
        drivetrain=drivetrain,
        scenario_reports=scenario_reports,
        selected_report=selected_report,
        formulas=_formula_catalogue(selected_report, drivetrain),
        provenance=tuple(report.provenance for report in scenario_reports),
        assumptions=(
            "The shaft is a homogeneous solid circular member with the approved 25 mm diameter.",
            "Wet-laundry eccentricity is represented as one rotating concentrated effective mass.",
            "Water retention applies the approved perforation-relief factor to a cylindrical drum volume.",
            "The transient factor approximates startup, pedal variation, and changing wash load.",
            "Material yield strength is the nominal galvanized-steel value used by the existing analytical model.",
        ),
        limitations=(
            "The model is a reduced-order analytical load estimate, not transient structural FEA or CFD.",
            "Bearing stiffness, welds, joints, fatigue, manufacturing variation, and detailed laundry motion are omitted.",
            "Hydrostatic and centrifugal pressure estimates do not model free-surface slosh or perforation flow in detail.",
        ),
        engineering_interpretation=_engineering_interpretation(selected_report),
        conclusion=_conclusion(selected_report),
        units_note=(
            "Calculations use SI base units. Angles use radians, speed is also shown in revolutions per minute, "
            "force in newtons, moment in newton-metres, stress and pressure in pascals (or megapascals for display), "
            "volume in cubic metres (or litres for display), mass in kilograms, and factors are dimensionless."
        ),
    )


def _build_scenario_report(
    scenario: OperatingScenario,
    fea_root: Path | str | None,
    *,
    include_fea: bool,
) -> ScenarioReport:
    results = calculate_scenario(scenario)
    package = _load_exact_cached_package(results, fea_root) if include_fea else None
    if package is None:
        return ScenarioReport(
            scenario=scenario,
            results=results,
            provenance=ANALYTICAL_PROVENANCE,
            fea_provenance=None,
            fea_package=None,
            fea_components=(),
            fea_summary=(),
        )
    fea_components = _fea_component_summaries(package)
    return ScenarioReport(
        scenario=scenario,
        results=results,
        provenance=ANALYTICAL_PROVENANCE,
        fea_provenance=SOLVED_FEA_PROVENANCE,
        fea_package=package,
        fea_components=fea_components,
        fea_summary=(
            f"Scenario: {scenario.name}",
            f"Provenance: {SOLVED_FEA_PROVENANCE}",
            f"Stage 1 package schema: {package.schema_version}",
            "Mesh levels: " + ", ".join(CANONICAL_FEA_MESH_LEVELS),
            *(_format_fea_component_summary(component) for component in fea_components),
            "The cached model is linear-static with reduced-order loads.",
        ),
    )


def _fea_component_summaries(
    package: Stage1FeaPackage,
) -> tuple[FeaComponentSummary, ...]:
    summaries: list[FeaComponentSummary] = []
    for name in ("shaft", "gear", "drum"):
        component = getattr(package, name)
        displacement_magnitude = np.linalg.norm(component.displacement_m, axis=2)
        minimum_factor = (
            None
            if component.nodal_factor_of_safety is None
            else float(np.min(component.nodal_factor_of_safety))
        )
        summaries.append(
            FeaComponentSummary(
                name=name,
                maximum_von_mises_pa=float(np.max(component.von_mises_pa)),
                maximum_displacement_m=float(np.max(displacement_magnitude)),
                minimum_factor_of_safety=minimum_factor,
                node_count=int(component.vertices_m.shape[0]),
                element_count=int(component.tetrahedra.shape[0]),
            )
        )
    return tuple(summaries)


def _format_fea_component_summary(component: FeaComponentSummary) -> str:
    factor_text = (
        "N/A"
        if component.minimum_factor_of_safety is None
        else f"{component.minimum_factor_of_safety:.4f}"
    )
    return (
        f"{component.name.title()}: maximum nodal von Mises stress "
        f"{component.maximum_von_mises_pa / 1.0e6:.3f} MPa; maximum nodal displacement "
        f"{component.maximum_displacement_m * 1.0e3:.4f} mm; minimum nodal factor of safety "
        f"{factor_text}; {component.node_count} nodes and {component.element_count} tetrahedral elements."
    )


def _load_exact_cached_package(
    results: ScenarioResults, fea_root: Path | str | None
) -> Stage1FeaPackage | None:
    """Load only Normal's deterministic cache path; never enumerate or solve."""

    if results.scenario.name != "Normal":
        return None
    root = (
        Path(fea_root)
        if fea_root is not None
        else Path(__file__).resolve().parent / "fea_results"
    )
    package_path = root / solver_request_hash(results.inputs, CANONICAL_FEA_MESH_LEVELS)
    if not package_path.is_dir():
        return None
    try:
        package = load_stage1_package_read_only(package_path)
    except (OSError, ValueError):
        return None
    if package_matches_request(package, results.inputs, CANONICAL_FEA_MESH_LEVELS):
        return package
    return None


def _project_dimensions() -> tuple[SymbolDefinition, ...]:
    return (
        SymbolDefinition("R", f"effective drum radius ({DRUM_EFFECTIVE_RADIUS_M:.3f} m)", "m", "shared geometry"),
        SymbolDefinition("L", f"drum depth ({DRUM_DEPTH_M:.3f} m)", "m", "shared geometry"),
        SymbolDefinition("d", f"solid shaft diameter ({SHAFT_DIAMETER_M:.3f} m)", "m", "shared geometry"),
        SymbolDefinition("L_shaft", f"shaft length ({SHAFT_LENGTH_M:.3f} m)", "m", "shared geometry"),
        SymbolDefinition("r_g", f"rear sprocket pitch radius ({GEAR_PITCH_RADIUS_M:.4f} m)", "m", "shared geometry"),
        SymbolDefinition("T_r", f"rear sprocket tooth count ({GEAR_TOOTH_COUNT:d})", "teeth", "existing drivetrain constant"),
    )


def _formula_catalogue(
    selected: ScenarioReport,
    drivetrain: DriveResult,
) -> tuple[FormulaDefinition, ...]:
    results = selected.results
    inputs = results.inputs
    analytical = results.analytical
    scenario = selected.scenario
    analytical_formulas = (
        FormulaDefinition(
            "drivetrain_speed_ratio",
            "Drivetrain Speed Ratio",
            r"N_{d,practical} = N_p(T_f/T_r), \qquad T_{r,ideal} = T_f(N_p/N_{d,target})",
            "<div>N<sub>d,practical</sub> = N<sub>p</sub>(T<sub>f</sub>/T<sub>r</sub>); "
            "T<sub>r,ideal</sub> = T<sub>f</sub>(N<sub>p</sub>/N<sub>d,target</sub>)</div>",
            f"N_d,practical = ({drivetrain.pedal_rpm:.1f} RPM) x "
            f"({drivetrain.front_teeth:d} teeth / {drivetrain.practical_rear_teeth:d} teeth) = "
            f"{drivetrain.actual_drum_rpm:.3f} RPM; T_r,ideal = ({drivetrain.front_teeth:d} teeth) x "
            f"(({drivetrain.pedal_rpm:.1f} RPM) / ({scenario.speed_rpm:.3f} RPM)) = "
            f"{drivetrain.exact_rear_teeth:.3f} teeth; N_d,target = {scenario.speed_rpm:.3f} RPM.",
            (
                _symbol("N_d,practical", "practical drum speed from the selected integer sprocket", "RPM", "calculated drivetrain value"),
                _symbol("N_d,target", "fixed target drum speed used by the scenario analysis", "RPM", "fixed scenario"),
                _symbol("N_p", "pedal cadence", "RPM", "existing drivetrain constant"),
                _symbol("T_f", "front chainring tooth count", "teeth", "existing drivetrain constant"),
                _symbol("T_r", "rear sprocket tooth count", "teeth", "existing drivetrain constant"),
                _symbol("T_r,ideal", "ideal non-integer rear sprocket tooth count", "teeth", "calculated drivetrain value"),
            ),
            "The chain ratio converts pedal cadence to a practical drum speed. The selected integer rear sprocket can differ slightly from the fixed target; analytical scenario calculations use N_d,target, while N_d,practical reports the buildable drivetrain outcome.",
        ),
        FormulaDefinition(
            "angular_speed_and_edge_velocity",
            "Angular Speed And Drum-Edge Velocity",
            r"\omega = 2\pi N_{d,target}/60, \qquad v_{edge} = \omega R",
            "<div>&omega; = 2&pi;N<sub>d,target</sub>/60; v<sub>edge</sub> = &omega;R</div>",
            f"omega = (2 pi rad/rev) x ({scenario.speed_rpm:.3f} rev/min) x (1 min / 60 s) = "
            f"{analytical.angular_speed_rad_s:.4f} rad/s; v_edge = ({analytical.angular_speed_rad_s:.4f} rad/s) x "
            f"({inputs.drum_radius_m:.3f} m) = "
            f"{analytical.angular_speed_rad_s * inputs.drum_radius_m:.4f} m/s.",
            (
                _symbol("omega", "angular speed", "rad/s", "calculated value"),
                _symbol("N_d,target", "fixed target drum speed used by the scenario analysis", "RPM", "fixed scenario"),
                _symbol("R", "effective drum radius", "m", "shared geometry"),
                _symbol("v_edge", "drum-edge tangential velocity", "m/s", "calculated value"),
            ),
            "The fixed target scenario speed is converted to angular speed before calculating drum-edge velocity as a transparent wash-agitation indicator. It is not a CFD prediction of the water or fabric velocity field.",
        ),
        FormulaDefinition(
            "human_power_torque_and_chain_force",
            "Human Power, Torque, And Chain Force",
            r"T_{nom}=P/\omega, \qquad T_{design}=K_tT_{nom}, \qquad F_{chain}=T_{design}/r_g",
            "<div>T<sub>nom</sub> = P/&omega;; T<sub>design</sub> = K<sub>t</sub>T<sub>nom</sub>; "
            "F<sub>chain</sub> = T<sub>design</sub>/r<sub>g</sub></div>",
            f"T_nom = ({scenario.human_power_w:.3f} W) / ({analytical.angular_speed_rad_s:.4f} rad/s) = "
            f"{analytical.nominal_torque_n_m:.4f} N m; T_design = ({scenario.transient_factor:.3f}) x "
            f"({analytical.nominal_torque_n_m:.4f} N m) = {analytical.design_torque_n_m:.4f} N m; "
            f"F_chain = ({analytical.design_torque_n_m:.4f} N m) / ({inputs.gear_pitch_radius_m:.4f} m) = "
            f"{analytical.chain_force_n:.2f} N.",
            (
                _symbol("P", "human mechanical power", "W", "fixed scenario"),
                _symbol("T_nom", "nominal shaft torque", "N m", "calculated value"),
                _symbol("K_t", "transient design factor", "dimensionless", "fixed scenario"),
                _symbol("T_design", "design shaft torque", "N m", "calculated value"),
                _symbol("F_chain", "chain force", "N", "calculated value"),
                _symbol("r_g", "rear sprocket pitch radius", "m", "shared geometry"),
                _symbol("omega", "angular speed", "rad/s", "calculated value"),
            ),
            "The transient factor raises nominal pedal torque to cover startup, pedal variation, and changing load before calculating the chain force at the rear sprocket.",
        ),
        FormulaDefinition(
            "water_volume_mass_and_weight",
            "Water Volume, Mass, And Weight",
            r"V_{drum}=\pi R^2L, \quad V_{retained}=V_{drum}f_{fill}(1-r_{relief}), "
            r"\quad m_{water}=\rho V_{retained}, \quad W_{water}=m_{water}g",
            "<div>V<sub>drum</sub> = &pi;R<sup>2</sup>L; V<sub>retained</sub> = V<sub>drum</sub>f<sub>fill</sub>(1-r<sub>relief</sub>); "
            "m<sub>water</sub> = &rho;V<sub>retained</sub>; W<sub>water</sub> = m<sub>water</sub>g</div>",
            f"V_drum = pi x ({inputs.drum_radius_m:.3f} m)^2 x ({inputs.drum_depth_m:.3f} m) = "
            f"{analytical.full_drum_volume_m3:.6f} m^3; V_retained = ({analytical.full_drum_volume_m3:.6f} m^3) x "
            f"({inputs.fill_fraction:.3f}) x (1 - {inputs.perforation_relief:.3f}) = "
            f"{analytical.retained_water_volume_m3:.6f} m^3; m_water = ({inputs.water_density_kg_m3:.1f} kg/m^3) x "
            f"({analytical.retained_water_volume_m3:.6f} m^3) = {analytical.retained_water_mass_kg:.3f} kg; "
            f"W_water = ({analytical.retained_water_mass_kg:.3f} kg) x ({inputs.gravity_m_s2:.5f} m/s^2) = "
            f"{analytical.retained_water_weight_n:.2f} N.",
            (
                _symbol("V_drum", "full cylindrical drum volume", "m^3", "calculated value"),
                _symbol("V_retained", "retained water volume", "m^3", "calculated value"),
                _symbol("R", "effective drum radius", "m", "shared geometry"),
                _symbol("L", "drum depth", "m", "shared geometry"),
                _symbol("f_fill", "water-fill fraction", "dimensionless", "fixed scenario"),
                _symbol("r_relief", "perforation relief fraction", "dimensionless", "existing engineering input"),
                _symbol("rho", "water density", "kg/m^3", "existing engineering input"),
                _symbol("m_water", "retained water mass", "kg", "calculated value"),
                _symbol("g", "gravitational acceleration", "m/s^2", "existing engineering input"),
                _symbol("W_water", "retained water weight", "N", "calculated value"),
            ),
            "The retained-water estimate reduces the sealed-cylinder volume for drum perforations. It is a reduced-order mass model, not a flow simulation.",
        ),
        FormulaDefinition(
            "water_pressure",
            "Hydrostatic And Centrifugal Water Pressure",
            r"p_h=\rho ghK_s, \qquad p_c=(\rho\omega^2R^2f_{fill}(1-r_{relief}))/2, \qquad p_{design}=p_h+p_c",
            "<div>p<sub>h</sub> = &rho;ghK<sub>s</sub>; p<sub>c</sub> = (&rho;&omega;<sup>2</sup>R<sup>2</sup>f<sub>fill</sub>(1-r<sub>relief</sub>))/2; "
            "p<sub>design</sub> = p<sub>h</sub> + p<sub>c</sub></div>",
            f"p_h = ({inputs.water_density_kg_m3:.1f} kg/m^3) x ({inputs.gravity_m_s2:.5f} m/s^2) x "
            f"({analytical.maximum_water_depth_m:.4f} m) x ({inputs.slosh_amplification:.3f}) = "
            f"{analytical.hydrostatic_pressure_pa:.2f} Pa; p_c = (({inputs.water_density_kg_m3:.1f} kg/m^3) x "
            f"({analytical.angular_speed_rad_s:.4f} rad/s)^2 x ({inputs.drum_radius_m:.3f} m)^2 x "
            f"({inputs.fill_fraction:.3f}) x (1 - {inputs.perforation_relief:.3f})) / 2 = "
            f"{analytical.centrifugal_pressure_pa:.2f} Pa; p_design = ({analytical.hydrostatic_pressure_pa:.2f} Pa) + "
            f"({analytical.centrifugal_pressure_pa:.2f} Pa) = "
            f"{analytical.design_water_pressure_pa:.2f} Pa.",
            (
                _symbol("p_h", "amplified hydrostatic pressure", "Pa", "calculated value"),
                _symbol("p_c", "centrifugal pressure estimate", "Pa", "calculated value"),
                _symbol("p_design", "combined design pressure", "Pa", "calculated value"),
                _symbol("h", "maximum water depth", "m", "calculated value"),
                _symbol("K_s", "slosh amplification", "dimensionless", "existing engineering input"),
                _symbol("rho", "water density", "kg/m^3", "existing engineering input"),
                _symbol("g", "gravitational acceleration", "m/s^2", "existing engineering input"),
                _symbol("omega", "angular speed", "rad/s", "calculated value"),
                _symbol("R", "effective drum radius", "m", "shared geometry"),
                _symbol("f_fill", "water-fill fraction", "dimensionless", "fixed scenario"),
                _symbol("r_relief", "perforation relief fraction", "dimensionless", "existing engineering input"),
            ),
            "Hydrostatic pressure comes from gravity head, while centrifugal pressure comes from rotation. Adding them provides a conservative transparent design-pressure estimate.",
        ),
        FormulaDefinition(
            "unbalanced_wet_laundry_load",
            "Unbalanced Wet-Laundry Load",
            r"F_u=m_ue\omega^2, \qquad F_y(\theta)=F_u\cos(\theta), \qquad F_z(\theta)=F_u\sin(\theta), \qquad \theta(t)=\omega t+\theta_0",
            "<div>F<sub>u</sub> = m<sub>u</sub>e&omega;<sup>2</sup>; F<sub>y</sub>(&theta;) = F<sub>u</sub>cos(&theta;); "
            "F<sub>z</sub>(&theta;) = F<sub>u</sub>sin(&theta;); &theta;(t) = &omega;t + &theta;<sub>0</sub></div>",
            f"F_u = ({scenario.laundry_mass_kg:.3f} kg) x ({scenario.eccentricity_m:.3f} m) x "
            f"({analytical.angular_speed_rad_s:.4f} rad/s)^2 = {results.imbalance_force_n:.3f} N; "
            f"theta(0.000 s) = ({analytical.angular_speed_rad_s:.4f} rad/s) x (0.000 s) + (0.000 rad) = "
            f"0.000 rad; F_y(0.000 rad) = ({results.imbalance_force_n:.3f} N) x cos(0.000 rad) = "
            f"{results.imbalance_force_n:.3f} N; F_z(0.000 rad) = ({results.imbalance_force_n:.3f} N) x "
            "sin(0.000 rad) = 0.000 N.",
            (
                _symbol("F_u", "imbalance-force magnitude", "N", "calculated value"),
                _symbol("m_u", "effective unbalanced wet-laundry mass", "kg", "fixed scenario"),
                _symbol("e", "centre-of-mass eccentricity", "m", "fixed scenario"),
                _symbol("omega", "angular speed", "rad/s", "calculated value"),
                _symbol("F_y", "y-direction rotating force component", "N", "calculated value"),
                _symbol("F_z", "z-direction rotating force component", "N", "calculated value"),
                _symbol("theta", "drum phase angle", "rad", "calculated state"),
                _symbol("t", "time", "s", "animation state"),
                _symbol("theta_0", "initial phase angle", "rad", "animation state"),
            ),
            "The unbalanced force rotates with the drum, creating cyclic bearing and shaft loading. Its direction changes with phase even though its magnitude remains constant in this model.",
        ),
        FormulaDefinition(
            "shaft_bending_and_torsion",
            "Shaft Bending And Torsion",
            r"M_{chain}=F_{chain}e_{chain}, \quad M_{reaction}=F_{reaction}e_{reaction}, \quad M_u=F_ue_{reaction}, "
            r"\quad M_{total}=M_{chain}+M_{reaction}+M_u, \quad \sigma_b=32M_{total}/(\pi d^3), \quad \tau_t=16T_{design}/(\pi d^3)",
            "<div>M<sub>chain</sub> = F<sub>chain</sub>e<sub>chain</sub>; M<sub>reaction</sub> = F<sub>reaction</sub>e<sub>reaction</sub>; "
            "M<sub>u</sub> = F<sub>u</sub>e<sub>reaction</sub>; M<sub>total</sub> = M<sub>chain</sub> + M<sub>reaction</sub> + M<sub>u</sub>; "
            "&sigma;<sub>b</sub> = 32M<sub>total</sub>/(&pi;d<sup>3</sup>); &tau;<sub>t</sub> = 16T<sub>design</sub>/(&pi;d<sup>3</sup>)</div>",
            f"M_chain = ({analytical.chain_force_n:.4f} N) x ({inputs.chain_force_overhang_m:.3f} m) = "
            f"{analytical.shaft_chain_bending_moment_n_m:.4f} N m; M_reaction = "
            f"({analytical.shaft_transverse_reaction_n:.4f} N) x ({inputs.shaft_reaction_overhang_m:.3f} m) = "
            f"{analytical.shaft_reaction_bending_moment_n_m:.4f} N m; M_u = ({results.imbalance_force_n:.4f} N) x "
            f"({inputs.shaft_reaction_overhang_m:.3f} m) = {results.imbalance_moment_n_m:.4f} N m; "
            f"M_total = ({analytical.shaft_chain_bending_moment_n_m:.4f} N m) + "
            f"({analytical.shaft_reaction_bending_moment_n_m:.4f} N m) + ({results.imbalance_moment_n_m:.4f} N m) = "
            f"{results.total_moment_n_m:.4f} N m; sigma_b = 32 x ({results.total_moment_n_m:.4f} N m) / "
            f"(pi x ({inputs.shaft_diameter_m:.3f} m)^3) = {results.bending_stress_pa:.3e} Pa; "
            f"tau_t = 16 x ({analytical.design_torque_n_m:.4f} N m) / "
            f"(pi x ({inputs.shaft_diameter_m:.3f} m)^3) = {analytical.shaft_torsional_shear_pa:.3e} Pa.",
            (
                _symbol("M_chain", "chain-force bending moment", "N m", "calculated value"),
                _symbol("M_reaction", "transverse-reaction bending moment", "N m", "calculated value"),
                _symbol("M_u", "laundry-imbalance bending moment", "N m", "calculated value"),
                _symbol("M_total", "total shaft bending moment", "N m", "calculated value"),
                _symbol("F_chain", "chain force", "N", "calculated value"),
                _symbol("F_reaction", "shaft transverse reaction", "N", "existing engineering input"),
                _symbol("F_u", "imbalance-force magnitude", "N", "calculated value"),
                _symbol("e_chain", "chain-force overhang", "m", "existing engineering input"),
                _symbol("e_reaction", "reaction-load overhang", "m", "existing engineering input"),
                _symbol("d", "solid shaft diameter", "m", "shared geometry"),
                _symbol("sigma_b", "outer-fibre bending stress", "Pa", "calculated value"),
                _symbol("tau_t", "maximum torsional shear stress", "Pa", "calculated value"),
                _symbol("T_design", "design shaft torque", "N m", "calculated value"),
            ),
            "Solid circular-shaft equations provide a transparent hand-calculation cross-check. The laundry term adds cyclic bending to the existing chain and reaction load model.",
        ),
        FormulaDefinition(
            "combined_stress_and_factor_of_safety",
            "Combined Stress And Factor Of Safety",
            r"\sigma_{vm}=\sqrt{\sigma_b^2+3\tau_t^2}, \qquad FoS_y=S_y/\sigma_{vm}",
            "<div>&sigma;<sub>vm</sub> = &radic;(&sigma;<sub>b</sub><sup>2</sup> + 3&tau;<sub>t</sub><sup>2</sup>); "
            "FoS<sub>y</sub> = S<sub>y</sub>/&sigma;<sub>vm</sub></div>",
            f"sigma_vm = sqrt(({results.bending_stress_pa:.3e} Pa)^2 + 3 x "
            f"({analytical.shaft_torsional_shear_pa:.3e} Pa)^2) = {results.von_mises_pa:.3e} Pa; "
            f"FoS_y = ({inputs.shaft_material.yield_strength_pa:.3e} Pa) / ({results.von_mises_pa:.3e} Pa) = "
            f"{results.factor_of_safety:.3f}.",
            (
                _symbol("sigma_vm", "von Mises equivalent stress", "Pa", "calculated value"),
                _symbol("sigma_b", "outer-fibre bending stress", "Pa", "calculated value"),
                _symbol("tau_t", "maximum torsional shear stress", "Pa", "calculated value"),
                _symbol("FoS_y", "yield factor of safety", "dimensionless", "calculated value"),
                _symbol("S_y", "shaft material yield strength", "Pa", "material property"),
            ),
            "A value above one indicates that this analytical stress estimate is below nominal yield. It does not remove the need to assess fatigue, joints, bearings, defects, and omitted dynamics.",
        ),
    )
    fea_formulas = (
        (_fea_result_formula(selected),)
        if selected.fea_package is not None
        else ()
    )
    return analytical_formulas + fea_formulas


def _fea_result_formula(report: ScenarioReport) -> FormulaDefinition:
    if report.fea_package is None or report.fea_provenance is None:
        raise ValueError("FEA result definitions require a solved package")
    mesh_levels = report.fea_package.assumptions["request_identity"]["mesh_levels"]
    mesh_text = ", ".join(str(level) for level in mesh_levels)
    evaluated_components = []
    for component in report.fea_components:
        factor_text = (
            "N/A"
            if component.minimum_factor_of_safety is None
            else f"{component.minimum_factor_of_safety:.4f}"
        )
        evaluated_components.append(
            f"{component.name}: sigma_vm,max = {component.maximum_von_mises_pa:.6e} Pa "
            f"({component.maximum_von_mises_pa / 1.0e6:.3f} MPa); "
            f"u_max = {component.maximum_displacement_m:.6e} m "
            f"({component.maximum_displacement_m * 1.0e3:.4f} mm); "
            f"FoS_min = {factor_text}; n_node = {component.node_count} nodes; "
            f"n_element = {component.element_count} elements"
        )
    return FormulaDefinition(
        identifier="fea_result_definitions",
        title=f"{report.scenario.name} Cached Stage 1 FEA Result Definitions",
        latex=(
            r"\sigma_{vm,max,c}=\max_i(\sigma_{vm,c,i}), \quad "
            r"u_{max,c}=\max_j\sqrt{u_{x,c,j}^2+u_{y,c,j}^2+u_{z,c,j}^2}, \quad "
            r"FoS_{min,c}=\min_j(FoS_{c,j})"
        ),
        html=(
            "<div>&sigma;<sub>vm,max,c</sub> = max<sub>i</sub>(&sigma;<sub>vm,c,i</sub>); "
            "u<sub>max,c</sub> = max<sub>j</sub>&radic;(u<sub>x,c,j</sub><sup>2</sup> + "
            "u<sub>y,c,j</sub><sup>2</sup> + u<sub>z,c,j</sub><sup>2</sup>); "
            "FoS<sub>min,c</sub> = min<sub>j</sub>(FoS<sub>c,j</sub>)</div>"
        ),
        evaluated=(
            f"scenario = {report.scenario.name}; provenance = {report.fea_provenance}; "
            f"mesh_level = {mesh_text}; "
            + "; ".join(evaluated_components)
            + "."
        ),
        symbols=(
            _symbol("sigma_vm,max,c", "maximum nodal von Mises stress for component c", "Pa", "exact cached FEA package"),
            _symbol("sigma_vm,c,i", "nodal von Mises stress at stress sample i for component c", "Pa", "exact cached FEA package"),
            _symbol("u_max,c", "maximum nodal displacement magnitude for component c", "m", "exact cached FEA package"),
            _symbol("u_x,c,j", "x displacement at node j for component c", "m", "exact cached FEA package"),
            _symbol("u_y,c,j", "y displacement at node j for component c", "m", "exact cached FEA package"),
            _symbol("u_z,c,j", "z displacement at node j for component c", "m", "exact cached FEA package"),
            _symbol("FoS_min,c", "minimum nodal factor of safety for component c", "dimensionless", "exact cached FEA package"),
            _symbol("FoS_c,j", "factor of safety at node j for component c", "dimensionless", "exact cached FEA package"),
            _symbol("n_node,c", "node count for component c", "nodes", "exact cached FEA package"),
            _symbol("n_element,c", "tetrahedral element count for component c", "elements", "exact cached FEA package"),
            _symbol("mesh_level", "solver mesh level", "dimensionless", "exact cached FEA request identity"),
            _symbol("c", "component identifier (shaft, gear, or drum)", "dimensionless", "exact cached FEA package"),
            _symbol("i", "nodal stress sample index", "dimensionless", "exact cached FEA package"),
            _symbol("j", "mesh node index", "dimensionless", "exact cached FEA package"),
        ),
        explanation=(
            f"For {report.scenario.name}, these extrema and mesh counts carry {report.fea_provenance} "
            "provenance from the exact cached Stage 1 package. The model is linear-static with reduced-order "
            "loads; nodal maxima are not transient structural analysis or CFD."
        ),
    )


def _symbol(symbol: str, meaning: str, unit: str, source: str) -> SymbolDefinition:
    return SymbolDefinition(symbol, meaning, unit, source)


def _engineering_interpretation(selected: ScenarioReport) -> str:
    results = selected.results
    analytical_text = (
        f"{selected.provenance}: {selected.scenario.name} produces {results.imbalance_force_n:.1f} N of rotating "
        "wet-laundry force and "
        f"a combined shaft von Mises estimate of {results.von_mises_pa / 1.0e6:.2f} MPa. "
        f"The corresponding analytical yield factor of safety is {results.factor_of_safety:.2f}."
    )
    if selected.fea_provenance is None:
        return analytical_text
    return (
        analytical_text
        + f" Separately, cached component metrics carry {selected.fea_provenance} provenance."
    )


def _conclusion(selected: ScenarioReport) -> str:
    analytical_text = (
        f"For the {selected.scenario.name} operating point, scenario equations and supplemental shaft checks "
        f"retain {selected.provenance} provenance."
    )
    fea_text = (
        ""
        if selected.fea_provenance is None
        else f" Exact cached component metrics retain separate {selected.fea_provenance} provenance."
    )
    return (
        analytical_text
        + fea_text
        + " These results support transparent concept comparison, with detailed dynamic, fatigue, bearing, "
        "and joint validation still required before fabrication."
    )


__all__ = [
    "ANALYTICAL_PROVENANCE",
    "CANONICAL_FEA_MESH_LEVELS",
    "CORE_FORMULA_IDS",
    "FeaComponentSummary",
    "FormulaDefinition",
    "LIMITATIONS_NOTE",
    "ReportDocument",
    "SOLVED_FEA_PROVENANCE",
    "ScenarioReport",
    "SymbolDefinition",
    "build_report_document",
    "core_formulas",
]
