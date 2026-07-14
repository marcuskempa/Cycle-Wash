# CycleWash Technical Evaluation Simplification Design

## Purpose

Simplify the Technical Evaluation page for an introductory engineering presentation while correcting the Three.js assembly orientation and the placement of the schematic water and unbalanced-laundry load.

## Approved User Experience

The Technical Evaluation becomes one continuous Streamlit page with no Presentation/Technical Report tabs. Information appears once, outside the embedded viewer, except for controls that must update continuously without causing a Streamlit rerun.

Page order:

1. Operating-scenario segmented control.
2. One compact row of selected-scenario metrics.
3. Interactive STL assembly viewer.
4. Four core engineering calculations.
5. Gentle/Normal/Heavy comparison table.
6. One concise limitations note.
7. PDF and offline HTML download buttons.

The viewer contains only the 3D canvas, Play/Pause control, phase slider, and playback-speed control. It does not repeat the scenario name, RPM, load values, provenance, headings, captions, or metrics already shown by Streamlit.

## Assembly Coordinate System

All exported STL meshes share the original Blender assembly reference whose origin is centered on the rear gear shaft. This shared gear-shaft origin remains authoritative and is never replaced by an individual mesh centroid.

- Rotation axis: Blender X.
- Vertical axis: Blender Z.
- Gravity direction: negative Blender Z.
- Rotation pivot: shared rear gear-shaft origin inferred from the shaft geometry.
- Rotating parts: inner drum, agitator, shaft, and gear.

The Three.js camera uses Z-up coordinates. The floor grid lies in the Blender XY plane. Mesh vertices are not globally rotated to compensate for the camera.

## Water And Laundry Placement

The shared origin defines the pivot, while `Inner_Drum.stl` vertex bounds define the usable internal display envelope.

The payload will include the normalized inner-drum geometric center and span. These values locate the schematic contents along the shaft without changing the pivot.

### Water

- The water volume remains in world space so gravity stays visually downward while the drum rotates.
- Its center is derived from the inner-drum geometric envelope.
- Its dimensions are limited to a conservative fraction of the drum depth and radius so it cannot protrude through the front or rear.
- Its vertical position and scale represent the selected fill fraction.
- A small bounded rotation around Blender X represents schematic slosh.

### Unbalanced Laundry

- The laundry marker belongs to the rotating group.
- Its axial coordinate is the inner-drum geometric midpoint expressed relative to the shared shaft pivot.
- Its radial Y/Z offset equals the selected scenario eccentricity.
- Rotating the group around Blender X therefore produces circular motion inside the drum without translating the pivot.
- The imbalance arrow begins at the laundry marker and uses the same rotating radial reference frame.

## Streamlit Technical Content

The combined page retains only four core calculations:

1. Gear ratio and practical drum speed.
2. Angular speed and drum-edge velocity.
3. Unbalanced wet-laundry force.
4. Combined shaft stress and factor of safety.

Each calculation shows a compact LaTeX equation, one evaluated substitution, and short variable/unit definitions. The full formula catalogue, repeated provenance blocks, extensive assumptions, cached-FEA detail, and repeated conclusions are removed from the page.

The single limitations statement will explain that the values are simplified analytical estimates for an introductory design study, not validated structural FEA or CFD, and do not contain enough material, boundary-condition, contact, turbulence, or mesh detail for engineering validation.

## Two-Page PDF

The PDF is limited to exactly two pages.

### Page 1

- Project title and concise purpose.
- Static snapshot of the STL washer assembly in the corrected orientation.
- Selected-scenario metrics.
- Compact Gentle/Normal/Heavy comparison table.

### Page 2

- The four core equations and evaluated values.
- Compact variable and unit definitions.
- Short engineering interpretation and conclusion.
- One limitations statement.

The PDF does not include the long formula catalogue, exploded schematic, separate FEA section, repeated assumptions, or repeated analytical-provenance warnings.

## Offline HTML

The downloadable HTML remains a single offline file. It includes the corrected interactive assembly and the same concise report content outside the viewer. Scenario controls remain available in the standalone HTML because Streamlit is not present there. The embedded Streamlit viewer continues to use the outer Streamlit scenario selector as its only scenario control.

## Error Handling

- Missing or malformed required STL files continue to produce concise actionable errors.
- Invalid drum bounds or non-finite geometry prevent report/viewer generation rather than placing contents arbitrarily.
- Existing offline size and triangle budgets remain enforced.

## Verification

- Unit tests verify Z-up camera/grid configuration and the shared shaft pivot.
- Unit tests verify water and laundry placement is derived from inner-drum bounds and remains within the drum envelope.
- Streamlit tests verify one combined page, no tabs, no duplicated selected metrics, and four core equations.
- PDF tests verify exactly two pages, the four equations, one limitations statement, and no removed long-form sections.
- Browser QA verifies the corrected orientation, contained water/laundry, playback controls, desktop layout, and absence of console errors.
- PDF pages are rendered to images and visually inspected for clipping, overlap, and readable density.
