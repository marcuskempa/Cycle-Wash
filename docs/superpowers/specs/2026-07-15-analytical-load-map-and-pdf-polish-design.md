# CycleWash Analytical Load Map And PDF Polish Design

## Purpose

Make the Technical Evaluation feel like a concise engineering report rather than a tutorial. Replace decorative animation coloring with a lightweight phase-resolved analytical load visualization, repair the static STL assembly image, and ensure Streamlit cannot serve an obsolete cached PDF after report-code changes.

## Approved Scope

This revision changes the Technical Evaluation viewer and its PDF export. It does not add a transient finite-element or computational-fluid solver. The existing Gentle, Normal, and Heavy scenario inputs and approved analytical results remain authoritative.

## Phase-Resolved Analytical Load Map

The animated viewer will calculate colors from the selected scenario and current drum phase instead of applying decorative cycling colors.

### Shaft And Gear

The shaft and gear use a phase-resolved estimated stress field. At a circumferential mesh position `phi` and drum phase `theta`, the local bending contribution is combined with the existing torsional shear estimate:

```text
sigma_b(phi, theta) = sigma_static + sigma_u cos(phi - theta)
sigma_vm(phi, theta) = sqrt(sigma_b(phi, theta)^2 + 3 tau_t^2)
```

`sigma_static` comes from the existing chain and reaction bending loads, `sigma_u` comes from the rotating laundry-imbalance moment, and `tau_t` comes from the existing design torque. The geometry continues to rotate around Blender X while the field is recomputed from phase. Colors use an engineering stress scale in MPa.

### Inner Drum

The inner drum uses a phase-resolved analytical pressure field in kPa. The field combines the existing hydrostatic and centrifugal pressure estimates with a bounded phase term representing the rotating wet-laundry load and schematic slosh. It is evaluated from each vertex position relative to the authoritative inner-drum envelope, so the higher-load region moves around the drum as phase changes.

### Other Components

The enclosure and door remain neutral and translucent. Water and the unbalanced-laundry marker remain inside the drum envelope. The laundry marker and load arrow rotate with the same phase used by the stress and pressure fields.

### Viewer Presentation

- Remove the sentence `Relative analytical load: animation color only, not solved FEA stress.`
- Add compact engineering legends for estimated shaft/gear stress in MPa and drum pressure in kPa.
- Keep the Gentle, Normal, Heavy, Play/Pause, phase, and playback-speed controls in the existing horizontal toolbar.
- Update the selected scenario, phase, and displayed values without a Streamlit rerun.
- Keep the viewer self-contained and offline-safe.

## Two-Page PDF

The report remains exactly two A4 pages.

### Page 1

- CycleWash title and one-paragraph purpose.
- A repaired static STL assembly image in the established Blender orientation.
- Selected Normal scenario metrics.
- Compact Gentle, Normal, and Heavy comparison table.

The assembly raster will depth-sort projected STL triangles, remove dense white triangle outlines, and use controlled opacity: enclosure translucent, rotating drum visible, shaft red, and gear amber. The result must clearly read as one assembled washer rather than sparse or exploded geometry.

### Page 2

- Four core calculation blocks.
- Each block shows one symbolic equation panel and one numerical-substitution equation panel using the same equation typography.
- Units stay inside numerical substitutions; separate symbol-definition paragraphs are removed.
- The unbalanced-load block retains the phase relationship `F_y = F_u cos(theta)` and `F_z = F_u sin(theta)` but does not include a static phase-arrow schematic.
- One short engineering interpretation and conclusion.
- One concise limitations sentence at the end.

The PDF excludes the long formula catalogue, repeated provenance explanations, detailed cached-FEA metrics, assumptions lists, tutorial-style symbol tables, and repeated FEA/CFD disclaimers.

## Cache Invalidation

Add an explicit PDF report fingerprint that changes when the PDF renderer or its report schema changes. Pass this fingerprint into the Streamlit PDF cache function, matching the existing viewer/offline-HTML asset-version pattern. This prevents a deployed app from returning an older cached multi-page report after imported PDF code changes.

## Error Handling

- Missing or malformed required STL files continue to produce the existing actionable export error.
- Invalid mesh coordinates or unusable projected bounds fail report generation instead of producing a misleading image.
- Analytical load fields clamp non-finite values and reject missing required geometry metadata.

## Verification

- Unit tests verify the PDF cache depends on the report fingerprint.
- PDF tests verify exactly two pages, exactly one limitations statement, no variable-definition paragraphs, and paired symbolic/numerical equation panels.
- Load-map tests verify phase changes alter the stress and pressure fields while preserving finite engineering units and expected extrema.
- Existing browser smoke tests continue to exercise scenario, phase, playback, and offline behavior.
- The generated PDF is rendered to PNG at presentation resolution and both pages are visually inspected for a recognizable assembly, readable equations, no clipping, and no layout defects.
- Browser QA checks the Normal scenario at multiple phases and confirms the stress/pressure legends and colors update without console errors.
