# CycleWash

CycleWash is a Streamlit engineering workspace for the bicycle-driven washer
drivetrain, structural-load visualization, and a fixed-scenario technical
evaluation.

## Run Locally

Install dependencies and launch the multipage application from the repository
root:

```powershell
python -m pip install -r requirements.txt
streamlit run Gear_Builder.py
```

`Gear_Builder.py` is the Streamlit entrypoint. The application provides three
pages:

1. `Gear_Builder.py` - drivetrain sizing and sprocket preview.
2. `pages/2_Structural_Load_Visualizer.py` - structural load visualization.
3. `pages/3_Technical_Evaluation.py` - technical evaluation and report exports.

## Technical Evaluation

The technical-evaluation page uses three approved fixed scenarios. They are
presentation constants, not editable solver inputs:

| Scenario | Drum speed | Human power | Water fill | Wet laundry | Eccentricity | Transient factor |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Gentle | 45 RPM | 100 W | 25% | 2.0 kg | 25 mm | 1.5 |
| Normal | 60 RPM | 150 W | 35% | 3.5 kg | 40 mm | 2.0 |
| Heavy | 50 RPM | 180 W | 45% | 5.0 kg | 60 mm | 2.5 |

The page provides a dark interactive assembly viewer, the complete technical
equation report, and cached `Download PDF Report` and `Download Offline HTML`
exports. The HTML export is self-contained for direct offline use.

Analytical scenario values are labeled `Analytical load estimate`. An exact
matching cached Normal scenario package, when available, is labeled `Solved
Stage 1 FEA`; it remains distinct from the analytical loads. The technical page
only reads that exact cache and never runs an FEA solver.

## Streamlit Community Cloud

Deploy with the repository root as the app path and this command:

```text
streamlit run Gear_Builder.py
```
