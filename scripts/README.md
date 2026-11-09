# Report figures and numbers

One command regenerates every data figure and every number quoted in the report:

    python scripts/report_figures.py --tunnel <path>/tunnel --team <path>/team6

- `analyse_team6.py`   -> results/metrics.tex, results/steps.tex, results/summary.txt
- `fig_demo_panels.py` -> figures/demo_{legend,a,b,c,d,e}.pdf, results/demo_panels.tex (Fig. 4)
- `report_figures.py`  -> runs both of the above, then figures/analysis_{a,b,c,d}.pdf (Fig. 5)

Outputs are written to `figures/` and `results/` next to this folder (the report's
Overleaf project layout). Requires numpy, scipy, pandas, matplotlib.
`--only-fig5` skips the first two steps.
