import sys
from pathlib import Path

# Lets `pytest cw2/pi/tests` (CI, from the repo root) import aircam, as running from cw2/pi does.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
