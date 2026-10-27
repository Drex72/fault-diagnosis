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

## Phase 2 final simulation run (27 Oct 2026)

No code changed. The run used commit `cd32167` throughout.
- `results/20261026-213554/`: the full simulated comparison, 850 sessions including H3 and H4 (`--final`); `results/phase2-final-run.log` is its terminal output.
- `docs/PROGRESS.md`: final status at the top (section 8 of `CLAUDE.md`) and the Phase 2 write-up with the per-scenario breakdown.
- `docs/OWNER_TODO.md`: step 21 names the chosen model (`qwen3:8b`) instead of a placeholder.

## Phase 1 model benchmark (26 Oct 2026)

- `faultdiag/agent.py`: new `LLMBadOutput`. An HTTP 5xx whose body says the server could not parse the model's reply (Ollama: "XML syntax error ... element <function> closed by </parameter>") now raises `LLMBadOutput` instead of `LLMUnavailable`; `Agent` counts it as an invalid call (`server_parse_errors`) and falls back to the engine at once, as for a timeout. Any other HTTP error is still fatal. Reason: the first benchmark run (`results/model-bench/20261026-144203`) stopped after 51 of 63 sessions when qwen3.5:9b wrote a malformed tool call in scenario H1: one bad answer from the model ended the whole run, and would have ended a long Phase 2 run the same way. The traceback in the terminal was the record of the old behaviour.
- `scripts/bench_models.py`: the summary and the printed table count these replies ("Unparseable").
- `gui/static/app.js`: the live session's model statistics show the count when it is not zero.
- New tests in `tests/test_agent.py` (2): the parse-error reply raises `LLMBadOutput` while another HTTP 500 is still `LLMUnavailable`; an agent run counts it and falls back without crashing.
- `faultdiag/runmeta.py`: `git_commit()` is taken once, when the module is imported, and cached. Reason: it was re-read from git for every model, so the first benchmark run labels its three models with three different commits (`5633da7`, `84cdb7a`, `07630e1+dirty`) although the process ran the code it loaded at 14:42 (`5633da7`) throughout: commits made while it ran relabelled later records. Records already written keep their stamps; `docs/model-benchmark.md` says which code each run actually used. New test in `tests/test_core.py`.
- `faultdiag/agent.py`: context trimming now starts at 36000 characters instead of 8000, and when it starts it compacts only the older calls: the latest 4 turns (the model's own calls, their results and the latest engine report) are still sent word for word. Reason: the benchmark on the Phase 2 agent (`results/model-bench/20261026-172933`) showed every session trimmed from about the fifth call, because 8000 characters is about 2.5k tokens while Ollama runs qwen3:8b with a 32768-token window (`context_length` in `/api/ps`). Once trimmed, the whole conversation became one user message and the model lost its own thread: on the same seeds, qwen3:8b's accuracy fell from 38% (8 of 21, agent before Phase 2) to 19% (4 of 21), mean cost rose from 15.8 to 25.6, and sessions that had ended correctly in 6 steps ran to the 20-step limit (F2 seed 5001: identical for 5 calls, then 15 more readings with the engine at p = 1.0). The `trimmed_turns` and `max_context_chars` statistics recorded by the Phase 2 logging were the evidence. Test updated; new test that a 20-step session is not trimmed.
- `faultdiag/harness.py`: `run_all(..., resume=False)`; with `resume=True` it reads the run directory's `sessions.jsonl`, keeps the recorded sessions and runs only the missing (scenario, seed, method) ones, appending to the same file; it writes `meta-resumed-<time>.json` beside the original `meta.json`. New helper `load_done`. Without `resume` nothing changes. Reason: the full Phase 2 run takes hours on a local model server, and the first benchmark run showed a whole run can stop on one server error; restarting from zero would waste the finished sessions.
- `scripts/run_experiment.py`: `--resume RUN_DIR`, refused unless the methods, scenarios, trials, `--final` and model match the original run's `meta.json`. New tests in `tests/test_core.py` (2).
- `scripts/bench_models.py`: `--engine-only` runs method C alone on the benchmark's scenarios and seeds and saves `engine_reference.json` (no model server needed); `--models` is required otherwise. Reason: the benchmark compares models with each other but had no reference for what the engine scores without a model on the same seeds, which is what the hybrid has to beat.
- `docs/RUNBOOK.md`: the llama-server command gains `-c 16384`, as used in the benchmark.
