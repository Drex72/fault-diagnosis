# Changelog

Every change to existing code, with the reason. Newest first within a phase.

## Phase 0 (26 Oct 2026)

- `faultdiag/runmeta.py` (new): provenance helpers (git commit, policy hash, Ollama model digest, Africa/Lagos timestamps). Reason: section 4 requires every record to carry them.
- `faultdiag/harness.py`: `run_trial` accepts `meta` (copied into the record, defaults to a sim run) and an optional `bench` (for the hardware runner later); `run_all` writes `meta.json`, appends each record to `sessions.jsonl` as it finishes, and logs the record fields; `summary_table` starts with a `Source:` line and refuses to mix sources. Reason: working rule 5 and section 4. Logging of the record fields was added first and showed the old records had no source, commit, policy hash or timestamp.
- `scripts/run_experiment.py`: passes the model name and Ollama digest per method; run directory named in Africa/Lagos time.
- `requirements.txt`: pinned `numpy==2.5.4`, `pytest==9.1.1`.
- `.gitignore` (new).

## Phase 1 and Phase 2 preparation (26 Oct 2026)

- `faultdiag/agent.py`: `OpenAICompatLLM` takes `extra` request fields; raises `LLMUnavailable` (server down or HTTP error) or `TimeoutError`. `Agent` counts a timeout as invalid and falls back to the engine; counts plain-text replies with no call (`no_call`) and `timeouts`. Reason: Phase 1 handling requirements. A demonstration before the change showed a single timeout crashed the whole trial.
- `faultdiag/harness.py`: `run_trial` re-raises `ConnectionError`; H3 and H4 added as test set `heldout_final`; `dev_scenarios()`; `run_all(final=...)` refuses the frozen set unless `final=True`.
- `scripts/bench_models.py`: rewritten (preflight, exit codes, warm-up, peak memory, saved results).
- `scripts/run_experiment.py`: `--final` flag; default scenarios are the development set.
- `faultdiag/faults.py`: `FaultState.current_gain` field (default empty).
- `faultdiag/simbench.py`: debug log of the injected sensor faults (added first; showed only tap offsets were supported), then applies `current_gain` to current readings.
- `faultdiag/heldout.py`: H3, H4.
- New tests: `tests/test_agent.py` (6), `tests/test_heldout_final.py` (4).

## Phase 2 (26 Oct 2026)

- `faultdiag/agent.py`: loop detection (the same call three times in a row with no new information ends the session; the model then gets one turn in which only `submit_diagnosis` is offered), context trimming (above 8000 characters the model gets the system prompt, a compact one-line-per-call history of every call, and the latest state with the latest engine report), evidence check on submission, and a per-turn `turns` log (reply text, call, validity) in the result for the GUI drill-down. Reason: Phase 2 requirements. Logging added first (context size per call, repeated calls, submitted evidence) showed, with a scripted model, the context growing about 1.6k characters per turn, six identical calls in a row going unnoticed, and evidence citing a tap that was never read and a probe that was never made being accepted.
- `faultdiag/evidence.py` (new): `check_evidence(evidence, history)`.

## Phase 3 (26 Oct 2026)

- `firmware/bench/` (new): PlatformIO project. `lib/benchcore` holds the protocol and every safety limit with no Arduino dependency; `src/main.cpp` is the board glue (ADS1115 behind the multiplexer, two INA226, test-load switch, NVS calibration, task watchdog, I2C bus recovery). 18 native unit tests (`pio test -e native`).
- `firmware/injector/` (new): fault-injection controller (11 relays, all-or-nothing `set`, at most 3 faults, random F4 slots, faults cleared after 10 s of host silence). 4 native unit tests.
- `faultdiag/serialbench.py` (new): `SerialBench` with the `SimBench.execute()` interface, `SerialLink` (pyserial), `cli_prober` for the human probe, `ToolFailures` for TF1 and TF2 on hardware.
- `faultdiag/mockdevice.py` (new): mock bench and injector firmware, `MockLink`.
- `faultdiag/injector.py` (new): `InjectorClient` and the scenario-to-relay map `HW_FAULTS`.
- `faultdiag/calib.py` (new): calibration fit, records, freshness check.
- `faultdiag/hwrun.py` (new): the hardware evaluation loop and grading, shared with the GUI.
- `faultdiag/methods.py` (new): builds methods A to E by name; the thinking-off request fields that `bench_models.py` used inline now live here too.
- `faultdiag/harness.py`: added `state_for_faults`, `run_blind_session`, `grade` and `scenario_table`. Existing functions unchanged. Reason: blind hardware sessions graded after the run, and the per-scenario breakdown Phase 2 asks for.
- `scripts/run_experiment.py`: methods built by `faultdiag/methods.py`; new flags `--server`, `--think`, `--timeout`, `--model-version`. Reason: the Phase 2 run must use the same thinking-off setting as the benchmark. Before the change the script sent no extra request fields (read from the code); `methods.py` now logs the request fields for every LLM method at the start of a run. Smoke run after the change: `results/20261026-152027` (2 scenarios, 1 trial, fixed/tree/bayes).
- `scripts/run_hardware.py`, `scripts/calibrate.py`, `scripts/fw_selftest.py`, `scripts/injector_check.py`, `scripts/refit_noise.py` (new). `FAULTDIAG_RESULTS_DIR` overrides where they write (used by the tests).
- `requirements.txt`: `pyserial==3.5`.
- New tests: `tests/test_serialbench.py` (19), `tests/test_hardware_scripts.py` (8).

## Phase 4 (26 Oct 2026)

- `gui/` (new): local dashboard, `python -m gui`. `server.py` (JSON API, server-sent events, static files), `store.py` (SQLite: live sessions, their event log, the project tracker), `live.py` (one live session at a time on the simulator, the mock bench or the real bench, with a shadow Bayesian engine for the hypothesis chart), `experiments.py` (read-only views over `results/` built from the CLI's own summary functions), `seed.py` (first-run tracker contents), `static/` (HTML, CSS, JavaScript; no external assets).
- `faultdiag/serialbench.py`: `safe_state()` now sets `inhibited` when the bench confirms, and logs it. Reason: the firmware latches the safe state until `resume`, but the host only learned of the latch from an over-current event, so the GUI showed a latched bench as ready and a following session would have met refused load tests. A GUI test written first showed `inhibited` still `False` after a confirmed safe state.
- `scripts/run_experiment.py`: `FAULTDIAG_RESULTS_DIR` replaces `results/` when set (default unchanged), as the hardware scripts already allow. Reason: the GUI tests run the real CLI into a temporary directory and compare its `summary.md` with the GUI's.
- `faultdiag/hwrun.py`: the summary text of `grade_dir` moved into `graded_summary(graded)`; `grade_dir` writes the same file as before. Reason: the GUI shows exactly that text without writing to the run directory.
- `faultdiag/methods.py`: `build_methods(..., on_reply=None)` wraps the model client so each reply also goes to a callback. Without a callback nothing changes. Reason: the live page shows the model's replies as they arrive.
- New tests: `tests/test_gui.py` (12).

## Phase 5 (26 Oct 2026)

- `README.md`: rewritten as a quick start for a new person (tests, a simulated experiment, the dashboard, local models, the mock hardware path), with the current layout and scenario sets. The old status table and early results moved out: current results live in `docs/PROGRESS.md`.
- `docs/ARCHITECTURE.md`, `docs/RUNBOOK.md` (new).
- `.github/workflows/ci.yml` (new): pytest on every push and pull request, Python 3.12, simulation and mock devices only.
