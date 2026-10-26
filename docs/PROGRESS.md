# Progress

## Phase 0: set up and baseline (done 26 Oct 2026)

- venv created with Python 3.12, requirements installed and pinned.
- `python -m pytest -q`: 17 passed.
- `python scripts/run_experiment.py --trials 10` run at commit `d384d33`. Output in `results/20261026-114057/` (`source: sim`).
- git initialised, `.gitignore` added, first commit made.
- No ESP32 board detected (`pio device list` shows only Bluetooth ports).

Baseline, simulation only (results/20261026-114057, 10 trials per scenario):

| Test set | Fixed (A) | Tree (B) | Bayes (C) |
|---|---|---|---|
| single (n=90) | 91% | 91% | 94% |
| compound (n=20) | 50% | 0% | 80% |
| heldout H1, H2 (n=20) | 0% | 0% | 0% (out-of-model flag 45%, confidently wrong 55%) |
| toolfail (n=20) | 45% | 45% | 55% |

These match the numbers in the README from the earlier development run.
