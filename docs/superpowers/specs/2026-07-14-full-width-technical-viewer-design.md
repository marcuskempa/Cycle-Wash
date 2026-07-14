# CycleWash Full-Width Technical Viewer Design

## Purpose

The deployed Technical Evaluation currently shows an older tabbed, split-column presentation and a blank 3D canvas. The page will be consolidated into one continuous technical presentation with a full-width working viewer and all engineering content below it.

## Goals

- Restore reliable 3D assembly rendering on Streamlit Community Cloud.
- Use one continuous page with no Presentation/Technical Report tabs.
- Give the 3D assembly the full available content width.
- Keep Gentle, Normal, and Heavy as a Streamlit scenario control so one selection updates the viewer, calculations, metrics, comparison, and exports together.
- Place playback controls in a compact horizontal toolbar above the canvas.
- Keep report content concise and ordered for an introductory engineering presentation.
- Preserve the existing two-page PDF and offline HTML exports.

## Non-Goals

- Do not build a custom bidirectional Streamlit component.
- Do not move scenario state exclusively into the iframe.
- Do not change the approved scenario values, formulas, STL coordinate system, or analytical assumptions.
- Do not add new FEA or CFD capabilities.

## Page Layout

The Streamlit page uses this order:

1. `CycleWash Technical Evaluation` title.
2. Gentle/Normal/Heavy segmented control.
3. Full-width embedded 3D viewer.
4. One compact five-value metric row for the selected scenario.
5. Four core engineering calculations with evaluated substitutions and variable/unit definitions.
6. Compact three-scenario comparison table.
7. One limitations note.
8. PDF and offline HTML download actions.

All report text is outside the iframe. The viewer does not repeat scenario metrics, report headings, provenance, or explanatory captions.

## Viewer Layout

The iframe contains two vertical regions:

1. A single horizontal playback toolbar.
2. The full-width 3D canvas filling the remaining iframe height.

The toolbar contains, from left to right:

- Play/Pause button.
- Phase slider and degree output.
- Playback-speed selector.

On narrow screens, the toolbar may wrap into two rows, but the canvas remains below it and spans the full iframe width. There is no permanent right-side control column and no unused blank band beneath the canvas.

## State And Data Flow

The Streamlit Gentle/Normal/Heavy control remains the source of truth. Changing it reruns the page, rebuilds or retrieves the cached fixed-scenario viewer HTML, and updates every metric and calculation below the viewer.

Playback state remains local to the iframe so animation does not cause Streamlit reruns. The iframe receives one selected scenario in its embedded payload and exposes only playback, phase, and speed controls.

## Rendering Reliability

The viewer remains self-contained and must not depend on external JavaScript, texture, or STL requests at runtime. The bundled Three.js asset and normalized STL geometry are embedded into the generated HTML.

Viewer startup must produce either:

- a rendered assembly and active controls; or
- a concise visible error status identifying the failed initialization step.

A blank canvas without a status message is not an acceptable state. Cache keys must change when the viewer template or embedded runtime changes so Streamlit Cloud cannot continue serving stale generated HTML after deployment.

## Deployment Contract

The public Streamlit application must be configured with:

- Repository: `marcuskempa/Cycle-Wash`
- Branch: `main`
- Entrypoint: `Gear_Builder.py`

The repository will include a short deployment note documenting these values. After the fix is pushed, the deployed app must be rebooted or redeployed and checked against the GitHub `main` commit.

## Error Handling

- Missing or malformed STL assets keep their existing actionable Streamlit error.
- Viewer runtime failures display inside the iframe instead of leaving an empty dark rectangle.
- Export failures remain isolated to the download area and do not remove the interactive page.
- Unsupported browser graphics produce a readable viewer status.

## Verification

- Unit tests verify the embedded viewer contains one horizontal toolbar and no split viewer/control columns.
- Unit tests verify scenario controls remain in Streamlit and that no presentation/report tabs return.
- Runtime smoke tests verify the self-contained HTML renders frames without network requests.
- Streamlit AppTest verifies one scenario selector, one viewer, one metric row, four core equations, one comparison table, and one limitations note.
- Browser QA covers desktop and 390 px mobile widths, all three scenarios, Play/Pause, phase changes, playback speed, and fresh console errors.
- Browser QA confirms the assembly is visible rather than merely checking that the canvas exists.
- Deployment verification confirms Streamlit Cloud is running `main` with `Gear_Builder.py`.
- Existing PDF tests continue to require exactly two pages and one limitations statement.

