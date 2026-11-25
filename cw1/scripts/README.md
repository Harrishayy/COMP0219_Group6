# Report figures and numbers

Install the dependencies once, then one command regenerates every data figure and every
logger-derived number in the report:

    pip install -r requirements.txt
    python scripts/report_figures.py --tunnel <path>/tunnel --team <path>/team6

| Script | Writes |
|---|---|
| `analyse_team6.py` | `results/metrics.tex` (numbers in the text, used as `\R{name}`), `results/steps.tex`, `results/summary.txt` |
| `fig_demo_panels.py` | `figures/demo_{legend,a,b,c,d,e}.pdf`, `results/demo_panels.tex` (Fig. 4) |
| `report_figures.py` | runs both of the above, then `figures/analysis_{a,b,c,d}.pdf` (Fig. 5) |

`--only-fig5` skips the first two steps. Outputs go to `figures/` and `results/` one level
above `scripts/` (the report's Overleaf layout); change `ROOT` at the top of each script if
your repository differs. The scripts expect the logger run names used for the Nov 4
calibration runs, the Nov 5 check run and the two assessed runs in `team6/`.

Not generated here: calibration constants (Table II) and calibration-stage comparisons come
from the calibration tool; firmware sizes and versions come from the PlatformIO build.
