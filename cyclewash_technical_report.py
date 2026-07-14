"""Immutable shared dataset for CycleWash technical-evaluation reports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from cyclewash_cad_calculator import DriveResult, calculate_cyclewash_drive
from cyclewash_dimensions import (
    DRUM_DEPTH_M,
    DRUM_EFFECTIVE_RADIUS_M,
    GEAR_PITCH_RADIUS_M,
    GEAR_TOOTH_COUNT,
    SHAFT_DIAMETER_M,
    SHAFT_LENGTH_M,
)
from cyclewash_fea_results import Stage1FeaPackage, load_stage1_package
from cyclewash_fea_runner import package_matches_request, solver_request_hash
from cyclewash_scenarios import SCENARIOS, OperatingScenario, ScenarioResults, calculate_scenario, scenario_by_name


CANONICAL_FEA_MESH_LEVELS: Final[tuple[str, ...]] = ("medium", "fine")
ANALYTICAL_PROVENANCE: Final[str] = "Analytical load estimate"
SOLVED_FEA_PROVENANCE: Final[str] = "Solved Stage 1 FEA"


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
class ScenarioReport:
    """Calculated scenario values and their structural-result provenance."""

    scenario: OperatingScenario
    results: ScenarioResults
    provenance: str
    fea_package: Stage1FeaPackage | None
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


def build_report_document(
    selected_name: str, fea_root: Path | str | None = None
) -> ReportDocument:
    """Build all report content without solving or waiting for FEA."""

    selected_scenario = scenario_by_name(selected_name)
    scenario_reports = tuple(
        _build_scenario_report(scenario, fea_root) for scenario in SCENARIOS
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
    scenario: OperatingScenario, fea_root: Path | str | None
) -> ScenarioReport:
    results = calculate_scenario(scenario)
    package = _load_exact_cached_package(results, fea_root)
    if package is None:
        return ScenarioReport(
            scenario=scenario,
            results=results,
            provenance=ANALYTICAL_PROVENANCE,
            fea_package=None,
            fea_summary=(),
        )
    return ScenarioReport(
        scenario=scenario,
        results=results,
        provenance=SOLVED_FEA_PROVENANCE,
        fea_package=package,
        fea_summary=(
            f"Stage 1 package schema: {package.schema_version}",
            "Mesh levels: " + ", ".join(CANONICAL_FEA_MESH_LEVELS),
            "The cached model is linear-static with reduced-order loads.",
        ),
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
        package = load_stage1_package(package_path)
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
    selected: ScenarioReport, drivetrain: DriveResult
) -> tuple[FormulaDefinition, ...]:
    results = selected.results
    inputs = results.inputs
    analytical = results.analytical
    scenario = selected.scenario
    return (
        FormulaDefinition(
            "drivetrain_speed_ratio",
            "Drivetrain Speed Ratio",
            r"N_d = N_p(T_f/T_r), \qquad T_{r,ideal} = T_f(N_p/N_{d,target})",
            "<div>N<sub>d</sub> = N<sub>p</sub>(T<sub>f</sub>/T<sub>r</sub>); "
            "T<sub>r,ideal</sub> = T<sub>f</sub>(N<sub>p</sub>/N<sub>d,target</sub>)</div>",
            f"N_d = {drivetrain.pedal_rpm:.1f} RPM x ({drivetrain.front_teeth:d} teeth / "
            f"{drivetrain.practical_rear_teeth:d} teeth) = {drivetrain.actual_drum_rpm:.3f} RPM; "
            f"T_r,ideal = {drivetrain.exact_rear_teeth:.3f} teeth.",
            (
                _symbol("N_d", "drum speed", "RPM", "calculated drivetrain value"),
                _symbol("N_p", "pedal cadence", "RPM", "existing drivetrain constant"),
                _symbol("T_f", "front chainring tooth count", "teeth", "existing drivetrain constant"),
                _symbol("T_r", "rear sprocket tooth count", "teeth", "existing drivetrain constant"),
            ),
            "The chain ratio converts pedal cadence to drum speed. The selected integer rear sprocket is practical, so its actual speed can differ slightly from the ideal target.",
        ),
        FormulaDefinition(
            "angular_speed_and_edge_velocity",
            "Angular Speed And Drum-Edge Velocity",
            r"\omega = 2\pi N_d/60, \qquad v_{edge} = \omega R",
            "<div>&omega; = 2&pi;N<sub>d</sub>/60; v<sub>edge</sub> = &omega;R</div>",
            f"omega = 2pi x {scenario.speed_rpm:.1f} RPM / 60 = {analytical.angular_speed_rad_s:.4f} rad/s; "
            f"v_edge = {analytical.angular_speed_rad_s:.4f} rad/s x {inputs.drum_radius_m:.3f} m = "
            f"{analytical.angular_speed_rad_s * inputs.drum_radius_m:.4f} m/s.",
            (
                _symbol("omega", "angular speed", "rad/s", "calculated value"),
                _symbol("N_d", "selected drum speed", "RPM", "fixed scenario"),
                _symbol("R", "effective drum radius", "m", "shared geometry"),
                _symbol("v_edge", "drum-edge tangential velocity", "m/s", "calculated value"),
            ),
            "Drum-edge velocity is a transparent wash-agitation indicator. It is not a CFD prediction of the water or fabric velocity field.",
        ),
        FormulaDefinition(
            "human_power_torque_and_chain_force",
            "Human Power, Torque, And Chain Force",
            r"T_{nom}=P/\omega, \qquad T_{design}=K_tT_{nom}, \qquad F_{chain}=T_{design}/r_g",
            "<div>T<sub>nom</sub> = P/&omega;; T<sub>design</sub> = K<sub>t</sub>T<sub>nom</sub>; "
            "F<sub>chain</sub> = T<sub>design</sub>/r<sub>g</sub></div>",
            f"T_nom = {scenario.human_power_w:.1f} W / {analytical.angular_speed_rad_s:.4f} rad/s = "
            f"{analytical.nominal_torque_n_m:.4f} N m; T_design = {scenario.transient_factor:.2f} x "
            f"{analytical.nominal_torque_n_m:.4f} N m = {analytical.design_torque_n_m:.4f} N m; "
            f"F_chain = {analytical.design_torque_n_m:.4f} N m / {inputs.gear_pitch_radius_m:.4f} m = "
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
            f"V_drum = pi x {inputs.drum_radius_m:.3f} m^2 x {inputs.drum_depth_m:.3f} m = "
            f"{analytical.full_drum_volume_m3:.6f} m^3; V_retained = {analytical.retained_water_volume_m3:.6f} m^3; "
            f"m_water = {analytical.retained_water_mass_kg:.3f} kg; W_water = {analytical.retained_water_weight_n:.2f} N.",
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
            f"p_h = {analytical.hydrostatic_pressure_pa:.2f} Pa; p_c = {analytical.centrifugal_pressure_pa:.2f} Pa; "
            f"p_design = {analytical.hydrostatic_pressure_pa:.2f} Pa + {analytical.centrifugal_pressure_pa:.2f} Pa = "
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
            f"F_u = {scenario.laundry_mass_kg:.3f} kg x {scenario.eccentricity_m:.3f} m x "
            f"({analytical.angular_speed_rad_s:.4f} rad/s)^2 = {results.imbalance_force_n:.3f} N; "
            f"at theta = 0 rad, F_y = {results.imbalance_force_n:.3f} N and F_z = 0.000 N.",
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
            f"M_chain = {analytical.shaft_chain_bending_moment_n_m:.4f} N m; M_reaction = "
            f"{analytical.shaft_reaction_bending_moment_n_m:.4f} N m; M_u = {results.imbalance_moment_n_m:.4f} N m; "
            f"M_total = {results.total_moment_n_m:.4f} N m; sigma_b = {results.bending_stress_pa:.3e} Pa; "
            f"tau_t = {analytical.shaft_torsional_shear_pa:.3e} Pa.",
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
            f"FoS_y = {inputs.shaft_material.yield_strength_pa:.3e} Pa / {results.von_mises_pa:.3e} Pa = "
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


def _symbol(symbol: str, meaning: str, unit: str, source: str) -> SymbolDefinition:
    return SymbolDefinition(symbol, meaning, unit, source)


def _engineering_interpretation(selected: ScenarioReport) -> str:
    results = selected.results
    return (
        f"{selected.scenario.name} produces {results.imbalance_force_n:.1f} N of rotating wet-laundry force and "
        f"a combined shaft von Mises estimate of {results.von_mises_pa / 1.0e6:.2f} MPa. "
        f"The corresponding analytical yield factor of safety is {results.factor_of_safety:.2f}."
    )


def _conclusion(selected: ScenarioReport) -> str:
    return (
        f"For the {selected.scenario.name} operating point, the shared analytical model reports "
        f"{selected.provenance.lower()}. The result is suitable for transparent concept comparison, "
        "with detailed dynamic, fatigue, bearing, and joint validation still required before fabrication."
    )


__all__ = [
    "ANALYTICAL_PROVENANCE",
    "CANONICAL_FEA_MESH_LEVELS",
    "FormulaDefinition",
    "ReportDocument",
    "SOLVED_FEA_PROVENANCE",
    "ScenarioReport",
    "SymbolDefinition",
    "build_report_document",
]
