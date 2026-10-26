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

## Phase 3: hardware (software done 26 Oct 2026; hardware runs waiting on the owner)

Done and tested without a board:

- Bench firmware (`firmware/bench/`) and injector firmware (`firmware/injector/`), PlatformIO. Their logic libraries have native unit tests: bench 18 passed, injector 4 passed (`pio test -e native`).
- Host transport `faultdiag/serialbench.py`, mock devices, injector client, calibration, the blind hardware runner and five scripts (`run_hardware.py`, `calibrate.py`, `fw_selftest.py`, `injector_check.py`, `refit_noise.py`).
- Tested end to end against mock devices, including over a real pseudo-terminal: 27 tests in `tests/test_serialbench.py` and `tests/test_hardware_scripts.py`.
- Mock runs (all labelled `sim`, under `results/mock-serial/`, git-ignored): firmware self-test script 13 of 13 checks passed; calibration fit and noise refit ran; full blind runner with fixed, tree and bayes on the 14 development scenarios, 1 trial each (`results/mock-serial/20261026-152225`).

Not yet run (no board connected, `pio device list` shows none):

| Hardware result | Status |
|---|---|
| Firmware self-test on a real board | not yet run |
| Calibration against the multimeter | not yet run |
| Noise and sigma refit from real data | not yet run (DECISIONS lists "after: not yet run") |
| Hardware development run | not yet run |
| Final hardware run with H3, H4 | not yet run |

The owner's steps are in `docs/OWNER_TODO.md`.

## Phase 4: GUI dashboard (done 26 Oct 2026, except a real-hardware session)

- `python -m gui` serves the dashboard on http://127.0.0.1:8765 with no internet. Pages: Live session, Experiments, Models, Calibration, Tracker.
- `tests/test_gui.py`: 12 tests against a real server on a free port. They cover every API endpoint, the event stream with `Last-Event-ID` replay and a second tab, a full simulator session, a full mock-bench session, the safe state and its latch, the human-probe reply, a server restart mid-session, the tracker edits and the Markdown export, and path traversal.
- The Experiments summary equals the CLI's: the test runs `run_experiment.py` and `run_hardware.py --mock` and compares byte for byte. Also checked by hand on two real runs (`results/20261026-152027`, `results/20261026-114057`): identical.
- Checked in a browser: a mock-bench session watched live (C2, Bayesian engine, diagnosed correctly at cost 3), the replay after a reload, the experiment detail, the tracker, and a phone-width layout.
- Full suite: 72 passed.
- Not done: watching a session on the real bench, because there is no board. The same code path runs with the mock bench (`mode: mock`); only the serial port differs.

## Phase 5: maintainer readiness (done 26 Oct 2026)

- `README.md` (quick start: venv, tests, a simulated experiment, the dashboard, local models, the mock hardware path), `docs/ARCHITECTURE.md` (components, data flow diagram, tool contract, safety layers), `docs/HARDWARE.md`, `docs/RUNBOOK.md` (demo from cold, offline checklist, local model servers, after calibration, troubleshooting), `docs/DECISIONS.md`, `docs/OWNER_TODO.md`, `CHANGELOG.md`.
- CI: `.github/workflows/ci.yml` runs pytest on every push and pull request (Python 3.12, simulation and mock devices only). First run on GitHub: 72 passed in 55 s (run 40344688569).
