# CycleWash Technical Evaluation Report Design

## Goal

Add a third Streamlit page that presents three fixed operating scenarios, an
analytical washer animation, an unbalanced-laundry load model, and matching PDF
and single-file offline HTML technical reports.

## Fixed Scenarios

| Scenario | Drum speed | Human power | Water fill | Wet laundry | Offset | Transient factor |
|---|---:|---:|---:|---:|---:|---:|
| Gentle | 45 RPM | 100 W | 25% | 2.0 kg | 25 mm | 1.5 |
| Normal | 60 RPM | 150 W | 35% | 3.5 kg | 40 mm | 2.0 |
| Heavy | 50 RPM | 180 W | 45% | 5.0 kg | 60 mm | 2.5 |

The scenarios are presentation constants rather than editable UI inputs. Normal
preserves the existing Stage 1 defaults so its current cached FEA package remains
eligible for exact matching.

## Calculation Architecture

`cyclewash_scenarios.py` owns immutable scenario definitions and supplemental
unbalanced-load results. It calls the existing `calculate_engineering_loads()`
without changing `EngineeringInputs` or `AnalyticalResults`, preserving the FEA
request schema and package hashes.

For wet-laundry mass `m_u`, effective eccentricity `e`, and angular speed `omega`:

```text
omega = 2 pi N / 60
F_u = m_u e omega^2
F_y = F_u cos(theta)
F_z = F_u sin(theta)
M_u = F_u e_reaction
sigma_b,total = 32 (M_chain + M_reaction + M_u) / (pi d^3)
sigma_vm,total = sqrt(sigma_b,total^2 + 3 tau_t^2)
FoS_y,total = S_y / sigma_vm,total
```

The report also includes the existing drivetrain torque, chain force, retained
water mass, hydrostatic pressure, centrifugal pressure, and shaft cross-checks.

## Result Provenance

Every displayed or exported structural value includes one of these labels:

- `Solved Stage 1 FEA`: an exact cached package matches the canonical scenario
  inputs and mesh request.
- `Analytical load estimate`: no exact matching solved package is used.
- `Relative analytical load`: animation color only; never presented as stress in
  pascals or as solved FEA.

Report generation never starts or waits for an FEA solve. Gentle and Heavy use
analytical results. Normal may include the exact cached Stage 1 result when found.

## Third Streamlit Page

Create `pages/3_Technical_Evaluation.py` backed by
`cyclewash_technical_evaluation_app.py`. The page contains:

1. A fixed Gentle/Normal/Heavy segmented selector.
2. A large dark-theme 3D assembly viewer with play, pause, phase, and playback
   speed controls.
3. Closed door geometry at 50% opacity; drum, shaft, and gear rotate together.
4. A gravity-referenced water body with bounded schematic slosh motion.
5. A visible wet-laundry mass and rotating imbalance vector.
6. Relative analytical load coloring with an explicit provenance caption.
7. Current scenario metrics and equations beside the viewer.
8. A three-scenario comparison table.
9. PDF and Offline HTML download buttons.

The in-app animation is schematic and deterministic. It visualizes the calculated
loads without claiming transient CFD or dynamic FEA fidelity.

## Shared Report Dataset

`cyclewash_technical_report.py` builds one immutable report document containing:

- Project dimensions and drivetrain configuration
- Gear ratio and fluid-edge velocity
- All three scenario inputs and calculated results
- Detailed selected-scenario equations
- Water and unbalanced-laundry loads
- Shaft bending, torsion, von Mises stress, and factor of safety
- Optional exact cached FEA summary
- Assumptions, limitations, engineering interpretation, and conclusion
- Static assembly/load illustrations for the PDF
- Interactive geometry and animation payloads for HTML

Both exporters consume this same document. They may format values differently but
must not recalculate engineering results independently.

## Formula Presentation And Definitions

The Streamlit page, PDF, and HTML must be educational engineering documents, not
result-only dashboards. Every calculation section contains all four elements:

1. A clean LaTeX-style display equation.
2. The evaluated equation with current numerical values and units substituted.
3. A symbol table defining every variable, its physical meaning, SI unit, and
   source (fixed scenario, shared geometry, material property, or calculated value).
4. A plain-language paragraph explaining what the equation models, why it is used,
   and how to interpret the result for CycleWash.

The Streamlit page uses `st.latex()` for display equations. The offline HTML uses
self-contained semantic HTML/CSS equation markup with Greek symbols, fractions,
subscripts, superscripts, and square roots; it must not depend on a MathJax or
KaTeX content-delivery network. The PDF uses embedded Unicode-capable fonts and
ReportLab equation blocks that visually match the HTML as closely as practical.

All internal calculations use SI base units. Display conversions are explicit and
never replace the canonical SI values. Each table identifies units in its headers.
The report includes a compact units note covering radians, revolutions per minute,
newtons, newton-metres, pascals, megapascals, cubic metres, litres, kilograms, and
dimensionless factors.

### Required Formula Catalogue

The report explains and evaluates, at minimum, the following relationships.

#### Drivetrain Speed Ratio

```text
N_d = N_p (T_f / T_r)
T_r,ideal = T_f (N_p / N_d,target)
```

Definitions: `N_d` drum speed (RPM), `N_p` pedal cadence (RPM), `T_f` front
chainring tooth count (teeth), and `T_r` rear sprocket tooth count (teeth). Explain
that the selected integer rear sprocket creates a practical speed that may differ
slightly from the ideal target.

#### Angular Speed And Drum-Edge Velocity

```text
omega = 2 pi N_d / 60
v_edge = omega R
```

Definitions: `omega` angular speed (rad/s), `N_d` drum speed (RPM), `R` effective
drum radius (m), and `v_edge` drum-edge tangential velocity (m/s). Explain why edge
velocity is used as a simple wash-agitation indicator rather than a CFD prediction.

#### Human Power, Torque, And Chain Force

```text
T_nom = P / omega
T_design = K_t T_nom
F_chain = T_design / r_g
```

Definitions: `P` human mechanical power (W), `T_nom` nominal torque (N*m), `K_t`
transient design factor (dimensionless), `T_design` design torque (N*m), `F_chain`
chain force (N), and `r_g` rear sprocket pitch radius (m). Explain that the transient
factor approximates startup, pedal variation, and load changes.

#### Water Volume, Mass, And Weight

```text
V_drum = pi R^2 L
V_retained = V_drum f_fill (1 - r_relief)
m_water = rho V_retained
W_water = m_water g
```

Definitions: `L` drum depth (m), `f_fill` water-fill fraction (dimensionless),
`r_relief` perforation relief fraction (dimensionless), `rho` water density (kg/m^3),
`m_water` retained water mass (kg), `g` gravitational acceleration (m/s^2), and
`W_water` retained water weight (N). Explain that retained volume is a reduced-order
estimate for a perforated rotating drum, not a sealed-cylinder volume.

#### Hydrostatic And Centrifugal Water Pressure

```text
p_h = rho g h K_s
p_c = (rho omega^2 R^2 f_fill (1 - r_relief)) / 2
p_design = p_h + p_c
```

Definitions: `p_h` amplified hydrostatic pressure (Pa), `h` maximum water depth
(m), `K_s` slosh amplification (dimensionless), `p_c` centrifugal pressure estimate
(Pa), and `p_design` combined design pressure (Pa). Explain the physical distinction
between gravity-driven head and rotation-driven radial pressure.

#### Unbalanced Wet-Laundry Load

```text
F_u = m_u e omega^2
F_y(theta) = F_u cos(theta)
F_z(theta) = F_u sin(theta)
theta(t) = omega t + theta_0
```

Definitions: `F_u` imbalance-force magnitude (N), `m_u` effective unbalanced wet
laundry mass (kg), `e` centre-of-mass eccentricity (m), `theta` drum phase angle
(rad), `t` time (s), and `theta_0` initial phase (rad). Explain that the force vector
rotates with the drum and produces cyclic bearing and shaft loading.

#### Shaft Bending And Torsion

```text
M_chain = F_chain e_chain
M_reaction = F_reaction e_reaction
M_u = F_u e_reaction
M_total = M_chain + M_reaction + M_u
sigma_b = 32 M_total / (pi d^3)
tau_t = 16 T_design / (pi d^3)
```

Definitions: `M_chain`, `M_reaction`, and `M_u` are bending-moment contributions
(N*m); `e_chain` and `e_reaction` are load-station lever arms (m); `d` is solid-shaft
diameter (m); `sigma_b` is outer-fibre bending stress (Pa); and `tau_t` is maximum
torsional shear stress (Pa). Explain why the solid circular-shaft equations are an
appropriate transparent hand-calculation cross-check.

#### Combined Stress And Factor Of Safety

```text
sigma_vm = sqrt(sigma_b^2 + 3 tau_t^2)
FoS_y = S_y / sigma_vm
```

Definitions: `sigma_vm` von Mises equivalent stress (Pa), `S_y` material yield
strength (Pa), and `FoS_y` yield factor of safety (dimensionless). Explain that a
factor greater than one indicates the analytical stress remains below nominal yield,
while a school-project design should still discuss uncertainty, fatigue, joints,
bearings, manufacturing defects, and omitted dynamic effects.

#### FEA Result Definitions

When an exact cached FEA package is included, define von Mises stress (Pa or MPa),
displacement magnitude (m or mm), nodal/element maxima, mesh level, and minimum
factor of safety. Explain that the Stage 1 model is linear-static with reduced-order
loads and is not transient structural analysis or CFD.

## Offline HTML Export

Generate one self-contained `.html` file. Embed CSS, JavaScript, the pinned local
Three.js bundle, STL-derived geometry, all scenario data, and report content. It
must contain no external HTTP resources and must work when opened directly from
disk. The viewer offers scenario selection, playback, phase scrubbing, and speed.

## PDF Export

Use ReportLab to generate a printable PDF without browser or system executables.
Include a title, executive summary, scenario comparison, equations, result tables,
static assembly/load illustrations, FEA provenance, assumptions, limitations, and
conclusion. No student/course cover fields are included because the PDF/HTML is a
technical evaluation supplement to an existing presentation.

The PDF must keep equations, evaluated substitutions, symbol definitions, and their
explanatory paragraphs together where practical. It must repeat table headers across
page breaks and avoid splitting a formula from its variable definitions.

## Verification

- Unit-test scenario constants and unbalanced-force equations.
- Test combined shaft stress and factor-of-safety calculations.
- Test that both exporters consume the same report document.
- Test HTML contains all scenarios, playback controls, embedded geometry, and no
  external URLs.
- Test PDF header, page count, and key report text.
- Test that every required formula has variable definitions and units in the shared
  report document and both exports.
- Run Streamlit AppTest on all three pages.
- Inspect the third page at desktop and mobile widths for overlap and readability.
