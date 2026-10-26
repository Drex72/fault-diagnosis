# CLAUDE.md: project brief and working rules

Read this whole file before doing anything. It is the standing brief for this repo and the handbook for anyone who maintains it.

**Mode: fully automated.** Run every phase in section 5 from start to finish without waiting for the owner. Do not stop to ask for approval between phases, for dependencies, for design choices or for restructuring. Make the decision, record it with the reason in `docs/DECISIONS.md`, and keep going. The only things you cannot do yourself are physical: wiring, buying parts, holding a multimeter. Section 7 says how to handle those without stopping.

## 1. What this project is

A SIWES (industrial placement) project for a final-year Electrical and Electronics Engineering student at the University of Lagos. A 12 V DC testbench (aged lead-acid battery, motor, lamp, resistive load, multiplexed voltage taps, INA226 current sensors, switched test load) is diagnosed by:

- A. a fixed-sequence procedure
- B. a decision tree
- C. Bayesian active diagnosis (expected information gain per unit cost)
- D. a local LLM agent alone
- E. a hybrid: the LLM plans on top of the Bayesian engine

The research question: does E beat C on faults outside the engine's model (held-out faults), on tool failures, and when a human measurement is needed? Results are reported whichever way they fall.

Everything runs offline. Inference is local (Ollama or llama.cpp). No cloud dependency at run time.

## 2. Current state (as of 26 Oct 2026)

Built and tested (17 tests pass):

- `faultdiag/circuit.py`: nodal-analysis bench model
- `faultdiag/measure.py`: instrument model
- `faultdiag/faults.py`: in-model faults F1 to F5
- `faultdiag/heldout.py`: held-out faults H1, H2 (only `harness.py` may import it)
- `faultdiag/tools.py`: tool contract and JSON Schema validator
- `faultdiag/guard.py` + `config/policy.json`: budget, limits, logging
- `faultdiag/simbench.py`: simulated bench
- `faultdiag/engine.py`: approach C
- `faultdiag/baselines.py`: approaches A, B
- `faultdiag/agent.py`: approaches D, E (tested only with a scripted model)
- `faultdiag/harness.py`, `scripts/run_experiment.py`, `scripts/bench_models.py`

Not started: model benchmark on real models, ESP32 firmware, serial transport, fault-injection controller, calibration, GUI, maintainer docs, CI.

Known engine weaknesses, visible in simulation. Do NOT tune them away to make results look better; they are findings:

- H2 (hidden parasitic load) is confidently misdiagnosed as lamp connection + motor stall.
- C2 and TF1 cannot be separated without the human probe.
- F4 (intermittent lamp) is sometimes missed because the stopping rule fires before the lamp opens.

## 3. Working rules (non-negotiable)

1. Never claim a test, benchmark or experiment ran unless you ran it in this session and show the actual output. If something could not be run, say so.
2. Before changing the behaviour of any existing module, add logging that shows the current behaviour, then change it. Errors must surface in the terminal while a run is happening, never only in a log file.
3. Prefer additive changes and thin wrappers over rewriting existing modules. You may restructure, rename or delete files when it is clearly better; record every change to existing code in `CHANGELOG.md` with the reason.
4. Keep code minimal. No framework, abstraction or dependency without a concrete need. Add dependencies when needed, pin them in `requirements.txt`, and note why in `docs/DECISIONS.md`.
5. Never mix simulation and hardware results. Every result record carries `source: "sim" | "hardware"`, and every table, chart and report labels which it is.
6. Held-out discipline: H1 and H2 have now been seen during development, so they are "development held-out". Before final evaluation, define at least two NEW held-out faults (for example main-fuse or wiring resistance, a failing INA226 on the source channel) in `heldout.py`, and do not look at agent behaviour on them, or tune prompts or thresholds against them, until the final run. Record the date they were frozen in `docs/DECISIONS.md`.
7. Safety: the AI and the GUI are never the only protection. The ESP32 firmware enforces every electrical limit on its own. No test on 230 V AC. If a hardware safety question is unclear, take the most conservative option, log the question in `docs/OPEN_QUESTIONS.md`, and continue. Never energise the test load on real hardware until the firmware safety self-test (section 5, Phase 3) has passed on that board.
8. Docs written for this project use plain hyphens, colons or commas, never em dashes.
9. Work in phases (section 5), in order, without pausing. At the end of each phase, run the tests, update `docs/PROGRESS.md` and `CHANGELOG.md`, commit with a clear message, then start the next phase immediately. If a phase's acceptance criteria cannot be met, record exactly why in `docs/PROGRESS.md` and move on to whatever does not depend on it.

## 4. Repository conventions

- Python 3.10+, numpy, pytest. Use a venv in `.venv` (git-ignored).
- Logging through `faultdiag/logsetup.py`. `FAULTDIAG_LOG=DEBUG` shows every tool call.
- Every result record also stores: git commit hash, policy file hash, model name and version (if any), seed, firmware version (hardware), and the ISO timestamp in Africa/Lagos time.
- `results/` holds raw run output (JSONL, append-only, never edited by hand). Derived tables are regenerated from it.
- `.gitignore`: `.venv/`, `__pycache__/`, `.pytest_cache/`, `*.sqlite` journal files, large raw logs if they exceed 50 MB.
- If no git remote is set and the `gh` CLI is authenticated, create a private GitHub repo under Drex72 and push after every phase. If `gh` is not available or not authenticated, keep committing locally and note it in `docs/OPEN_QUESTIONS.md`.

## 5. Phases and acceptance criteria

### Phase 0: set up and baseline
- Create the venv, install requirements, run `python -m pytest -q`, run `python scripts/run_experiment.py --trials 10`.
- `git init` if needed, add `.gitignore`, first commit.
- Done when: tests pass on this Mac and the baseline summary is saved and labelled `sim`.

### Phase 1: local model benchmark
- Check machine RAM (`sysctl hw.memsize`) and installed models (`ollama list`). Ollama is already installed; check it is running (`ollama serve` or the app).
- Shortlist 3 or 4 models that fit comfortably in RAM with headroom, including at least one 3B and one 7B/8B class model with tool-calling support. Include a 1-bit Bonsai-8B GGUF via llama.cpp if it runs on this Mac.
- Run `scripts/bench_models.py` on each. Record accuracy, invalid tool-call rate, fallbacks, median and p95 latency, and peak memory from `ollama ps`, in `docs/model-benchmark.md` with the exact model tags and date.
- Handle: Ollama not running (clear error, exit non-zero), model not pulled, request timeout (count as invalid, fall back), the model replying in plain text instead of a tool call (already parsed; count it separately).
- Done when: one model is chosen with the reason written down, or the doc says honestly that none was reliable enough, with the numbers.

### Phase 2: full simulated comparison
- Run A, B, C, D, E on all scenarios, 10+ trials each, with the chosen model.
- Add to the agent: loop detection (the same call three times in a row with no new information ends the session), context trimming for long sessions (keep the system prompt, the latest engine report and a compact history), and an evidence check (the evidence in `submit_diagnosis` must only cite readings that exist in the guard history; flag unverifiable claims in the record).
- Done when: `results/` has the run, the summary is labelled `sim`, and the per-scenario breakdown is written up in `docs/PROGRESS.md`.

### Phase 3: hardware
Firmware (PlatformIO, Arduino framework, `firmware/bench/`):
- Implement the tool contract in `faultdiag/tools.py` over USB serial at 115200 baud, as newline-delimited JSON. Request: `{"id": n, "tool": "...", "args": {...}}`. Response: `{"id": n, "status": "ok|error|timeout|safety_abort", "data": {...}, "error": "..."}`. On boot, send `{"event": "hello", "fw": "<version>", "calibration": "<id>"}`.
- Enforce in firmware, independently of the host: the test-load pulse width cap; a minimum cool-down between load tests (thermal limit on the test resistor); abort and switch off when source current exceeds the policy limit; refuse load tests when battery voltage is below a cutoff (about 11.5 V); a host heartbeat (if none arrives within a few seconds, all switched loads go off); a safe state on boot and reset; the hardware watchdog enabled.
- Handle: partial or corrupt JSON lines, unknown tools, an I2C bus hang (timeout and bus recovery), INA226 overflow, ADC saturation, multiplexer settling time, a blown fuse (all taps near 0 V: report it, don't diagnose through it), ESP32 brown-out when the motor stalls (power the ESP32 separately, with a common ground; document this).

Host side:
- `faultdiag/serialbench.py` with the same `execute()` interface as `SimBench`, using timeouts from `policy.json`. Handle reconnects, an ESP32 reset mid-session (detect the `hello` event, mark the session interrupted, never silently continue), and stale or duplicate response ids.
- `human_probe` on hardware: prompt the technician (CLI and GUI), with a timeout from policy.

Fault injection:
- A second ESP32 (`firmware/injector/`) switches faults from scenario scripts. Ground truth is written to a separate file that the diagnostic methods never read. Graded only after the session ends.

Calibration:
- `scripts/calibrate.py`: compare each tap and current channel against the handheld multimeter at several points, fit gain and offset, save a dated calibration record, and refuse hardware runs older than the latest calibration unless overridden.
- Re-fit the simulator noise values and the engine sigma values from calibration data. Record the before and after values in `docs/DECISIONS.md`.

Done when: the hardware test set runs end to end, with fewer trials than the simulation, and is labelled `hardware`.

### Phase 4: GUI dashboard
A local web app, served from this laptop, that works with no internet (no CDN; vendor every JS/CSS asset). Choose the stack yourself and record the choice in `docs/DECISIONS.md`. Requirements: it starts with one command, stores data locally (SQLite is a good default), shows live updates during a session, and has a clean, legible design that reads well in a project demo.

Pages:
1. **Live session:** bench connection status and firmware version; the current scenario (hidden on hardware runs until grading); every step with tool, arguments, reading, cost and budget left; the engine's top hypotheses as probabilities over time; the misfit flag and largest residuals; agent reasoning text; invalid calls and fallbacks; a human-probe panel where the technician types a reading; a stop-session button, plus an emergency safe-state button that sends the firmware's safe-state command (the firmware's own limits still apply if the GUI is closed).
2. **Experiments:** a list of runs with source (sim or hardware), date, commit, model and policy hash; the summary table per test set and method; a per-scenario breakdown; cost against accuracy; drill-down into any single session's step log; CSV export. Sim and hardware are never mixed in one chart.
3. **Models:** the benchmark table from Phase 1, with latency and invalid-rate history.
4. **Calibration:** the calibration records per channel, the latest fit, and a stale-calibration warning.
5. **Project tracker:** the 14-day plan as milestones with status, the bill of materials with procurement status (needed, ordered, received, tested; supplier; price in NGN; date), the lab equipment requests, and open issues and risks. Editable from the UI, stored in SQLite, exportable to Markdown.

Handle: a server restart mid-session (the session resumes or is clearly marked interrupted), several browser tabs open at once, a lost bench connection shown immediately, very long sessions, timestamps in Africa/Lagos time, and phone-width screens.

Done when: a full hardware session can be run and watched from the GUI, the experiments page reproduces the CLI summary numbers exactly, and the GUI has its own tests for the API endpoints.

### Phase 5: maintainer readiness
- `README.md`: quick start for a new person.
- `docs/ARCHITECTURE.md`: components, the data flow from bench to guard to engine and agent, the tool contract, the safety layers. Include one diagram.
- `docs/HARDWARE.md`: wiring, pin map, divider values, shunt values, fuse ratings, power arrangement, photos once built.
- `docs/RUNBOOK.md`: how to run the demo from cold, including the offline checklist (model pulled, Ollama running, calibration fresh, battery charged) and troubleshooting.
- `docs/DECISIONS.md`: dated decisions with reasons.
- `docs/PROGRESS.md`: what is done, what is next, known issues.
- `CHANGELOG.md`.
- CI: a GitHub Actions workflow that runs pytest on every push (simulation only).
- Done when: someone who has never seen the repo can run the tests, run a simulated experiment and open the GUI using only the docs.

## 6. Reporting results

When asked for results, give the numbers from the actual run files, with source, date and commit, and say what was not run. If the hybrid does not beat the Bayesian engine somewhere, say so plainly. That is an acceptable finding.

## 7. Physical steps you cannot do

Do not wait for them. Work around them in this order:

1. **No board connected:** build and compile all firmware (`pio run`), test the serial protocol against a mock device in the host test suite, and test `serialbench.py` end to end against that mock. Check for a board on each phase boundary (`pio device list`); if one appears, flash it and run the firmware self-test.
2. **Board connected, bench not wired:** run only the firmware self-test (protocol, watchdog, heartbeat cutoff, safe state on boot). Never energise loads.
3. **Anything needing a human** (wiring, buying parts, multimeter calibration readings, the human probe on real hardware): add it to the GUI's project tracker and to `docs/OWNER_TODO.md` as a numbered checklist with exact instructions (what to buy, where to connect what, which reading to take and where to type it). Keep that file current; it is the owner's only to-do list.
4. Hardware result tables stay empty and clearly marked "not yet run" until real runs exist. Never fill them with simulated numbers.

## 8. When everything automatable is done

Write a final status at the top of `docs/PROGRESS.md`: what was built, what was tested and how, the simulation results, what is waiting on the owner (pointing to `docs/OWNER_TODO.md`), and the exact commands to run the hardware evaluation and open the GUI once the bench is wired.
