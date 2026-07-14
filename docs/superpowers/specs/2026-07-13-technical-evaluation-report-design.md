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

## Verification

- Unit-test scenario constants and unbalanced-force equations.
- Test combined shaft stress and factor-of-safety calculations.
- Test that both exporters consume the same report document.
- Test HTML contains all scenarios, playback controls, embedded geometry, and no
  external URLs.
- Test PDF header, page count, and key report text.
- Run Streamlit AppTest on all three pages.
- Inspect the third page at desktop and mobile widths for overlap and readability.
