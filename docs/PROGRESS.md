# Progress

## Final status (27 Oct 2026)

Everything that can be done without the physical bench is done. The hardware runs wait on the owner.

**Built:**

- The five diagnosis methods (A fixed sequence, B decision tree, C Bayesian engine, D local LLM agent, E LLM planning on the engine), the simulated 12 V bench, the guard and the experiment harness (`faultdiag/`, `scripts/`).
- The agent additions: loop detection, context trimming, evidence check, and handling for a model server that is down, a model not pulled, reply timeouts, plain-text replies and replies the server cannot parse.
- Two new frozen held-out faults, H3 (main wiring resistance) and H4 (failing source current sensor), run once in simulation.
- Bench and injector ESP32 firmware (`firmware/`), with every electrical limit enforced on the board. The serial transport, mock devices, calibration, the blind hardware runner, and scripts for the firmware self-test, injector check and noise refit.
- The offline dashboard (`gui/`): live session, experiments, models, calibration, project tracker.
- Added after Phase 2, at the owner's request: Jev (TypeSafe's cloud decision model) as an optional planner for D and E (`--server jev`). Tested against a fake server, then run against the real service in simulation with the owner's key.
- Docs: `README.md`, `docs/ARCHITECTURE.md`, `docs/HARDWARE.md`, `docs/RUNBOOK.md`, `docs/DECISIONS.md`, `docs/model-benchmark.md`, `docs/OWNER_TODO.md`, `CHANGELOG.md`. CI on GitHub runs the tests on every push.

**Tested, and how (all run on 27 Oct 2026 unless noted):**

- `.venv/bin/python -m pytest -q`: 87 passed. This covers the simulator, engine, guard, agent (with a scripted model and local fake servers, including a fake Jev), serial transport and scripts (against mock devices, including over a real pseudo-terminal), and the dashboard API (against a real server).
- Firmware logic, native unit tests (`pio test -e native`): bench 18 passed, injector 4 passed. Both firmwares compile for the ESP32.
- Dashboard checked in a browser on 26 Oct with a mock-bench session, and at phone width.
- A fresh clone from GitHub, set up with only the README, passes the tests, runs a simulated experiment and starts the dashboard (details under Phase 5).
- Not tested: anything on a real board or bench. `pio device list` shows no ESP32.

**Simulation results** (`source: sim`, details under Phase 1 and Phase 2 below):

- Model benchmark: qwen3:8b chosen (33% as the hybrid's planner on 21 sessions). Bonsai-8B scored 19%, llama3.2:3b 0%, and the engine alone 52%.
- Full comparison (`results/20261026-213554`, commit `cd32167`, 850 sessions, 10 trials per scenario), correct out of 170: A 101, B 91, C 112, D 16, E 89.
- **The hybrid (E) does not beat the Bayesian engine (C) on any condition of the research question.**
  - Held-out faults: 0 of 40 each. C flags 13 as out of model; E is wrong in all 40.
  - Tool failures: E 8, C 11 of 20.
  - Human measurement needed (C2): E 1, C 6 of 10.
  - E beats C only on the intermittent lamp, F4: 10 against 7.
  - Over all paired sessions: E right where C was wrong 4 times; C right where E was wrong 27 times.
- The LLM alone (D) answers "ground return" in 161 of 170 sessions.
- With Jev as the planner (`results/20261027-103259`, commit `39143d5`, same scenarios and seeds), correct out of 170: D 30, E 99. **E still does not beat C**: right where C was wrong 5 times, wrong where C was right 18 times. Held-out 2 of 40 (C 0), tool failures 1 of 20 (C 11), C2 7 of 10 (C 6). Jev alone named the frozen H3 fault (`main_wiring`) in 7 of 10 sessions, using the human probe; no other method named it. Details under "After Phase 2: Jev".

**Waiting on the owner:** everything physical, as a numbered checklist in `docs/OWNER_TODO.md`, also shown on the dashboard's Project tracker page. In order:

1. Buy and borrow the parts.
2. Flash both boards.
3. Wire the bench.
4. Calibrate against the multimeter.
5. Run on the bench.

Every hardware result below is "not yet run". The optional Jev comparison (steps 24 to 28) has been run in simulation; the owner should replace the API key, which was shared in a chat.

**Commands once the bench is wired**, run from the repository folder; `<bench port>` and `<injector port>` come from `.pio-venv/bin/pio device list`:

```bash
.venv/bin/python -m gui --open
.venv/bin/python scripts/fw_selftest.py --port <bench port> --watchdog
.venv/bin/python scripts/calibrate.py --port <bench port> --meter "<meter make and model>"
.venv/bin/python scripts/run_hardware.py --port <bench port> --injector-port <injector port> --methods bayes --scenarios healthy --trials 2
.venv/bin/python scripts/run_hardware.py --port <bench port> --injector-port <injector port> --methods fixed,tree,bayes,llm,hybrid --model qwen3:8b --trials 3
.venv/bin/python scripts/run_hardware.py --port <bench port> --injector-port <injector port> --methods fixed,tree,bayes,llm,hybrid --model qwen3:8b --trials 3 --final
```

The first command opens the dashboard at http://127.0.0.1:8765. The others are, in order, `docs/OWNER_TODO.md` steps 6, 17, 20, 21 and 22: firmware self-test, calibration, healthy check, development run, and the final run with H3 and H4, once only. Ollama must be running with qwen3:8b pulled (`docs/RUNBOOK.md`, offline checklist). A hardware session can also be started and watched from the dashboard (Live session, Bench panel, "Real bench").

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

## Phase 1: local model benchmark (done 26 Oct 2026)

- Shortlist on this Mac (48 GiB): llama3.2:3b, qwen3:8b, qwen3.5:9b on Ollama 0.32.1, and the 1-bit Bonsai-8B GGUF on llama.cpp (it runs). Benchmarked as the hybrid's planner on 7 scenarios x 3 seeds, paired.
- Result (final run, agent at `30dffe4`): qwen3:8b 33%, Bonsai-8B 19%, llama3.2:3b 0%; the engine alone scores 52% on the same sessions. **qwen3:8b chosen**; no model beat the engine. qwen3.5:9b was left out on speed (median reply 27.8 s). Full tables, setup and caveats: `docs/model-benchmark.md`.
- Handling: server down (clear error, exit 2) and model not pulled (exit 3, the others still run), both checked by running the script on 26 Oct; in `tests/test_agent.py`: reply timeout (invalid call, fall back to the engine), plain-text reply instead of a tool call (parsed and counted), an unreachable server stopping the run, and, found during the benchmark, a malformed tool call the server cannot parse (invalid call, fall back; it had stopped the first run).
- Found and fixed during the benchmark: the first run stopped on that malformed call; run records were stamped with the wrong commit when commits landed mid-run; and the Phase 2 context trimming cut qwen3:8b from 38% to 19% until it was fixed. All three are in `CHANGELOG.md`.

## Phase 2: full simulated comparison (done 27 Oct 2026)

- Agent additions, tested in `tests/test_agent.py`: loop detection (the same call three times with no new information ends the session), context trimming (system prompt, latest engine report, compact history; fixed during Phase 1, see `docs/model-benchmark.md`) and the evidence check (readings cited in `submit_diagnosis` are checked against the guard history and flagged in the record).
- Also added: a run can be continued after an interruption (`run_experiment.py --resume RUN_DIR`), so the frozen H3 and H4 never have to be run twice. It was not needed.
- Run: `results/20261026-213554`, `source: sim`, commit `cd32167`, policy `88b28c1f4c8a`, qwen3:8b (`500a1f067a9f`), thinking off, 60 s per reply. 5 methods x 17 scenarios x 10 trials = 850 sessions, including the frozen H3 and H4 for the first and only time:
  `.venv/bin/python scripts/run_experiment.py --methods fixed,tree,bayes,llm,hybrid --model qwen3:8b --trials 10 --final --timeout 60`
- Started 26 Oct 21:35, finished 27 Oct 09:25 in one go. The sessions took 3.45 h; most of the rest was the laptop asleep (pauses of 2.7 h, 1.0 h and 3.3 h between sessions). Model errors 0, reply timeouts 0, unparseable replies 0.
- A, B and C give exactly the Phase 0 numbers on the shared scenarios (same seeds), so the simulator and engine did not change between the two runs.

Summary (simulation, from `results/20261026-213554/summary.md`):

| Test set | Method | n | Correct | Wrong | Inconclusive | Out-of-model flag | Mean cost |
|---|---|---|---|---|---|---|---|
| single | A fixed | 90 | 91% | 9% | 0% | 0% | 10.0 |
| single | B tree | 90 | 91% | 9% | 0% | 0% | 5.2 |
| single | C bayes | 90 | 94% | 6% | 0% | 0% | 6.8 |
| single | D llm | 90 | 7% | 93% | 0% | 0% | 18.8 |
| single | E hybrid | 90 | 80% | 10% | 10% | 0% | 12.7 |
| compound | A fixed | 20 | 50% | 50% | 0% | 0% | 10.0 |
| compound | B tree | 20 | 0% | 100% | 0% | 0% | 2.5 |
| compound | C bayes | 20 | 80% | 15% | 5% | 0% | 6.5 |
| compound | D llm | 20 | 0% | 100% | 0% | 0% | 18.9 |
| compound | E hybrid | 20 | 45% | 35% | 20% | 0% | 16.1 |
| held-out, development (H1, H2) | A fixed | 20 | 0% | 100% | 0% | 0% | 10.0 |
| held-out, development | B tree | 20 | 0% | 100% | 0% | 0% | 8.0 |
| held-out, development | C bayes | 20 | 0% | 55% | 0% | 45% | 5.4 |
| held-out, development | D llm | 20 | 50% | 50% | 0% | 0% | 18.8 |
| held-out, development | E hybrid | 20 | 0% | 100% | 0% | 0% | 16.9 |
| held-out, final (H3, H4) | A fixed | 20 | 0% | 100% | 0% | 0% | 10.0 |
| held-out, final | B tree | 20 | 0% | 100% | 0% | 0% | 8.0 |
| held-out, final | C bayes | 20 | 0% | 80% | 0% | 20% | 4.7 |
| held-out, final | D llm | 20 | 0% | 100% | 0% | 0% | 18.6 |
| held-out, final | E hybrid | 20 | 0% | 100% | 0% | 0% | 18.4 |
| tool failure | A fixed | 20 | 45% | 55% | 0% | 0% | 10.0 |
| tool failure | B tree | 20 | 45% | 55% | 0% | 0% | 5.8 |
| tool failure | C bayes | 20 | 55% | 25% | 20% | 0% | 13.8 |
| tool failure | D llm | 20 | 0% | 100% | 0% | 0% | 18.7 |
| tool failure | E hybrid | 20 | 40% | 35% | 25% | 0% | 16.9 |

Per scenario, correct of 10 (simulation):

| Scenario | Truth | A fixed | B tree | C bayes | D llm | E hybrid |
|---|---|---|---|---|---|---|
| healthy | none | 10 | 10 | 9 | 0 | 1 |
| F1 | battery | 10 | 10 | 10 | 3 | 10 |
| F1b | battery (marginal) | 10 | 10 | 10 | 0 | 9 |
| F2 | motor connection | 10 | 10 | 10 | 0 | 10 |
| F3 | motor stall | 10 | 10 | 10 | 0 | 10 |
| F4 | intermittent lamp | 2 | 2 | 7 | 3 | 10 |
| F5@motor | motor sensor offset | 10 | 10 | 9 | 0 | 5 |
| F5@lamp | lamp sensor offset | 10 | 10 | 10 | 0 | 7 |
| F5@load | load sensor offset | 10 | 10 | 10 | 0 | 10 |
| C1 | battery + motor stall | 10 | 0 | 10 | 0 | 8 |
| C2 | motor connection + motor sensor | 0 | 0 | 6 | 0 | 1 |
| H1 | ground return | 0 | 0 | 0 | 10 | 0 |
| H2 | unmonitored load | 0 | 0 | 0 | 0 | 0 |
| TF1 | motor connection, motor tap times out | 0 | 0 | 1 | 0 | 1 |
| TF2 | motor stall, motor current reads garbage | 9 | 9 | 10 | 0 | 7 |
| H3 | main wiring resistance | 0 | 0 | 0 | 0 | 0 |
| H4 | failing source current sensor | 0 | 0 | 0 | 0 | 0 |
| Total of 170 | | 101 | 91 | 112 | 16 | 89 |

**The research question, in simulation, with qwen3:8b: the hybrid (E) does not beat the Bayesian engine (C) on any of the three conditions.**

- **Held-out faults (H1 to H4, 40 sessions):** neither gets one right. C reports "out of model" in 13 of 40 (H1 9, H4 4) and is confidently wrong in the other 27. E is wrong in all 40, including the H1 sessions where the engine reported the misfit: there it answered battery plus lamp connection (8 of 10). E has no out-of-model answer by design (its prompt asks it to name the element outside the engine's model), and it never named the right one. On H3 both mostly answer "battery"; on H4 both mostly answer "lamp connection".
- **Tool failures (TF1, TF2, 20 sessions):** E 8, C 11. TF1: 1 each. TF2: E 7, C 10.
- **When a human measurement is needed (C2, TF1):** C2: E 1, C 6. E used the human probe in only 2 of these 20 sessions (1 C2, right; 1 TF1, wrong). C never uses the probe (it costs 10 of the 40 budget), so TF1 stays at 1 of 10, as the known weakness says.
- **Where E beats C:** F4, the intermittent lamp: 10 against 7. E spends more (mean cost 14.3 against 6.1) and is still measuring when the lamp opens, which is the engine's known F4 weakness.
- **Where E loses to C:** healthy (1 against 9: E reported a lamp connection fault in 9), F5@motor (5 against 9), F5@lamp (7 against 10), C1 (8 against 10), C2 (1 against 6), TF2 (7 against 10).
- Over all 170 paired sessions (same scenario and seed), E is right where C is wrong 4 times and C is right where E is wrong 27 times (two-sided sign test p = 3.4e-5). E also costs about twice as much (mean 14.7 against 7.2 of 40) and takes about 50 s a session against 0.4 s.

**The LLM alone (D)** got 16 of 170. It took a human probe in all 170 sessions, nearly always from the shared return to battery negative, read about 0.03 V and answered "ground return" in 161 of 170. Its 10 of 10 on H1, a ground-return fault, is that default answer matching the truth, not a diagnosis.

Agent statistics (from the records): D made 1516 model calls, none invalid, median reply 1.84 s, p95 7.92 s. E made 1663, 22 invalid (1.3%), and the engine fallback was never used (0 fallbacks); no loop was detected; context was trimmed in 11 of 170 sessions; 18 sessions ended without the model submitting and are graded inconclusive. The evidence check flagged 1 of E's 152 submissions, a false positive: the model cited the load's rated 24 ohm, not a reading.

The known engine weaknesses are unchanged and were not tuned: H2 is diagnosed as lamp connection plus motor stall in 10 of 10, TF1 is not separated from C2 without the probe, and F4 is missed 3 times in 10.

Caveats: simulation only; one model (8B, thinking off) chosen in Phase 1; 10 trials per scenario; the prompts were not changed after H3 and H4 were run. H3 and H4 have now been seen in simulation; the hardware final run (`docs/OWNER_TODO.md` step 22) still uses them once.

## After Phase 2: Jev as a cloud planner (27 Oct 2026; run in simulation)

- The owner asked to try Jev, TypeSafe's cloud decision model, before a general cloud model. `faultdiag/jev.py` puts it behind the agent's model interface: each turn Jev chooses the next call from the 31 concrete calls the tool contract allows (or submitting), then the fault set from 154 options. The agent loop, guard, engine fallback and loop detection are the same as for qwen3:8b. Reasons and details: `docs/DECISIONS.md`.
- Tested against a fake Jev server in `tests/test_jev.py` (9 tests, all passing): full D and E sessions, the key never in a record or log, a missing or refused key, rate limits, a service that stays down, answers outside the options.
- Run against the real Jev on 27 Oct 2026 with the owner's key, model `jev-1.14.0` (Jev's replies name `jev-1.14.0` as the model that answered), code at commit `39143d5`. All `source: sim`.

**Short benchmark** (the 21 Phase 1 sessions, seeds 5000 to 5002; `results/model-bench/20261027-103049`, log `results/jev-bench.log`): 8 of 21 correct (38%), against qwen3:8b 7 and the engine alone 11. Invalid calls 0%, median reply 0.67 s, p95 1.64 s, 138 requests, 549,431 input tokens. Row added to `docs/model-benchmark.md`.

**Full comparison** (D and E with Jev, all 17 scenarios, 10 trials, `--final`; `results/20261027-103259`, log `results/jev-final-run.log`; 340 sessions from 10:32 to 10:59, once, without `--resume`). The scenarios and seeds are the Phase 2 run's, so every session pairs with C and with qwen3:8b's D and E on the same seed (170 pairs per method). Correct, out of n:

| Test set | n | C (engine) | D qwen3:8b | E qwen3:8b | D Jev | E Jev |
|---|---|---|---|---|---|---|
| single | 90 | 85 | 6 | 72 | 17 | 79 |
| compound | 20 | 16 | 0 | 9 | 2 | 17 |
| heldout (H1, H2) | 20 | 0 | 10 | 0 | 5 | 2 |
| toolfail | 20 | 11 | 0 | 8 | 0 | 1 |
| heldout_final (H3, H4) | 20 | 0 | 0 | 0 | 6 | 0 |
| **total** | 170 | **112** | 16 | 89 | 30 | **99** |

Per scenario (correct of 10):

| Scenario | C | D qwen3:8b | E qwen3:8b | D Jev | E Jev |
|---|---|---|---|---|---|
| healthy | 9 | 0 | 1 | 5 | 10 |
| F1 | 10 | 3 | 10 | 6 | 9 |
| F1b | 10 | 0 | 9 | 2 | 8 |
| F2 | 10 | 0 | 10 | 3 | 10 |
| F3 | 10 | 0 | 10 | 0 | 10 |
| F4 | 7 | 3 | 10 | 1 | 3 |
| F5@motor | 9 | 0 | 5 | 0 | 10 |
| F5@lamp | 10 | 0 | 7 | 0 | 10 |
| F5@load | 10 | 0 | 10 | 0 | 9 |
| C1 | 10 | 0 | 8 | 0 | 10 |
| C2 | 6 | 0 | 1 | 2 | 7 |
| H1 | 0 | 10 | 0 | 5 | 2 |
| H2 | 0 | 0 | 0 | 0 | 0 |
| TF1 | 1 | 0 | 1 | 0 | 1 |
| TF2 | 10 | 0 | 7 | 0 | 0 |
| H3 | 0 | 0 | 0 | 6 | 0 |
| H4 | 0 | 0 | 0 | 0 | 0 |

What it shows:

- **With Jev planning, the hybrid still does not beat the engine.** E Jev 99, C 112. Over the paired sessions, E Jev was right where C was wrong 5 times, and C was right where E Jev was wrong 18 times (exact two-sided sign test, p = 0.011). By condition of the research question:
  - Held-out faults: E Jev 2 of 40 (both H1, answering `ground_return`), C 0. Neither got H2, H3 or H4.
  - Tool failures: E Jev 1 of 20, C 11. On TF2 (motor stalled, motor current sensor returning garbage) E Jev named the broken sensor `current_sensor_motor` in all 10 sessions, but paired it with a wrong second element in 7 (`ground_return` 6, `lamp_connection` 1) and with the stall in 3; the truth is the stall alone. On TF1 it answered healthy 9 times.
  - Human measurement needed (C2): E Jev 7, C 6 of 10; paired, 1 against 0. Too few to call a difference.
  - E Jev also misses the intermittent lamp (F4) more often than C: 3 against 7 (it answered healthy 7 times).
- **Jev is the better planner on top of the engine, but not significantly.** E Jev 99 against E qwen3:8b 89; paired 28 against 18 (p = 0.18). It never sent an invalid call (0 of 1,799 model calls), never fell back to the engine, never looped, and its median reply took 0.68 s (p95 1.60 s) against qwen3:8b's 4.7 s.
- **Jev alone (D) is still weak, but less one-track than qwen3:8b alone.** 30 against 16 of 170 (paired 24 against 10, p = 0.024). It answered `ground_return` in 60 of 170 sessions; qwen3:8b alone did in 161.
- **The one held-out success: Jev alone named the frozen H3 fault.** D Jev answered `main_wiring` in 7 of 10 H3 sessions (6 exactly right, 1 with `ground_return` added) and in no other scenario. Each of the 6 used the human probe. No method in Phase 2 (A to E) named `main_wiring` at all. With the engine's report in front of it (E), Jev followed the engine's in-model hypotheses instead (`battery` plus `ground_return` 6 times) and got none. On H1 too, D Jev was right in all 5 sessions where it used the probe and in none of the 5 where it did not. This is 10 sessions per scenario and suggests the engine's report pulls the planner towards in-model answers; it is an observation, not a tested claim.
- Probes: D Jev used the human probe in 115 of 170 sessions, E Jev in 51. Mean cost of 40: D Jev 11.9, E Jev 8.0; in Phase 2, C 7.2, D qwen3:8b 18.8, E qwen3:8b 14.7.

Secondary view, not the score: sessions that named at least one true element, on faulty scenarios. It shows where an answer was half right; the exact-set grading above stays the result.

| Test set | n | C | D qwen3:8b | E qwen3:8b | D Jev | E Jev |
|---|---|---|---|---|---|---|
| single (faulty) | 80 | 77 | 7 | 80 | 40 | 70 |
| compound | 20 | 20 | 0 | 20 | 18 | 20 |
| heldout | 20 | 0 | 10 | 0 | 5 | 5 |
| toolfail | 20 | 12 | 0 | 8 | 5 | 4 |
| heldout_final | 20 | 0 | 0 | 0 | 7 | 0 |

On H2 (hidden parasitic load) E Jev named `unmonitored_load` twice, the only sessions of any method to name it, but each time with `ground_return` added, so they count as wrong.

**For the research question nothing changes:** with either planner, E does not beat C on held-out faults, on tool failures, or when a human measurement is needed.

Caveats: simulation only; 10 trials per scenario; Jev is a cloud service, used only for this comparison (the demo and every default stay offline with qwen3:8b). Nothing was tuned for Jev: it gets the same tool descriptions and bench description as the chat models. H3 and H4 have now been run by a second planner, once, with nothing changed before or after. The full run's token use was not recorded (`run_experiment.py` does not save it; `bench_models.py` does). It made 2,139 requests (1,799 next-step choices plus 340 diagnoses); at the benchmark's average of about 4,000 input tokens per request that is about 8.5 million tokens, about $0.36 at the listed price, an estimate. The owner's TypeSafe dashboard showed no requests during the run, although every reply came from Jev with its probabilities and token counts; one tagged test request (`jev-latest`, 09:52:38 UTC, request id `req_01a17cc6b35c76a38b1f3685a9b7832b`) was sent so the owner can check the dashboard.

How it was compared: `jev_compare.py` (a throwaway script, not in the repo) pairs records by scenario and seed. To check any number above, read the two `sessions.jsonl` files: each record has `method`, `scenario`, `seed`, `truth`, `elements` and `correct`.

## After Phase 2: a top cloud model through OpenRouter (27 Oct 2026; built, not yet run)

- The upper-bound test the owner asked for after the Jev results: if even the strongest model available cannot make the hybrid beat the engine, the finding is about the design, not the model. `--server openrouter` sends the agent's usual chat requests to OpenRouter with an API key (`faultdiag/openrouter.py`); first model `anthropic/claude-opus-5.5`, which always reasons (`--think`) and ignores temperature. Reasons: `docs/DECISIONS.md`.
- Tested against a fake OpenRouter in `tests/test_openrouter.py` (11 tests, all passing), and the benchmark script end to end against the same fake in a scratch copy of the repository (2 sessions; tokens and cost recorded, the key in no output or file).
- Not run against the real service: it needs the owner's OpenRouter key and credits (`docs/OWNER_TODO.md` steps 30 to 33). Estimated cost of the 21-session short test on Opus 5.5: about 1 million input tokens (from qwen3:8b's context sizes on the same sessions), so about 4 dollars of input plus 1 to 7 dollars of reasoning and output; the script prints the real figure.

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
- Fresh-clone check (27 Oct 2026): cloned from GitHub into an empty folder and followed only the README quick start. Tests: 78 passed. `run_experiment.py --trials 10` printed the Phase 0 numbers. The dashboard started and served its page and `/api/status` (checked with curl on port 8799; not opened in a browser from the clone).
