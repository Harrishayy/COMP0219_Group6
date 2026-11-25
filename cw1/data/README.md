# data

Keep runs that matter for calibration or the report here, e.g. `data/cal/`, `data/runs/`, with a line in
this file saying what each one is (date, method, fan settings, ambient conditions).
`data/logs/` is the default output of `host/log_serial.py` and is git-ignored. Move a log out of it
to keep it.

`data/tunnel/` is where the Wind Tunnel Logger (`pnpm logger`) saves tunnel runs: `<run>_aligned.csv`,
`_vdas.csv` and `_student.csv`. The dashboard's Tunnel runs panel and `calib tunnel` read them from here.
