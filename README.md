# Autonomous Electrical Fault Diagnosis (SIWES)

Software for a 12 V DC diagnostic testbench: an aged lead-acid battery feeding a motor, a lamp and a resistive load, with multiplexed voltage taps, two INA226 current sensors and a switched test load. Five methods diagnose faults by choosing measurements under a cost budget:

| | Method | Code |
|---|---|---|
| A | fixed measurement sequence | `faultdiag/baselines.py` |
| B | decision tree | `faultdiag/baselines.py` |
| C | Bayesian active diagnosis (expected information gain per unit cost) | `faultdiag/engine.py` |
| D | local LLM agent alone | `faultdiag/agent.py` |
| E | hybrid: the LLM plans on top of the Bayesian engine | `faultdiag/agent.py` |

The research question: does E beat C on faults outside the engine's model (held-out faults), on tool failures, and when a human measurement is needed? Everything runs offline, with local models (Ollama or llama.cpp). The same tool contract drives a circuit simulator and the ESP32 bench.

Current results, what is done and what is waiting are in [docs/PROGRESS.md](docs/PROGRESS.md).

## Quick start (simulation, about 5 minutes)

Needs Python 3.10 or newer (3.12 is used here and in CI).

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
```

All tests run without hardware, a model or the internet.

Run the baselines and the Bayesian engine on every development scenario:

```bash
.venv/bin/python scripts/run_experiment.py --trials 10
```

It prints a summary labelled `sim` and saves `results/<date-time>/` (`sessions.jsonl`, `summary.md`, `meta.json`). Set `FAULTDIAG_LOG=DEBUG` to see every tool call.

Open the dashboard:

```bash
.venv/bin/python -m gui --open
```

It serves http://127.0.0.1:8765 from this laptop only. On the Live session page, pick "Simulator", a method and a scenario, and press Start session to watch a diagnosis step by step. The Experiments page shows every run in `results/`.

## With a local model (methods D and E)

```bash
ollama pull qwen3:8b
.venv/bin/python scripts/bench_models.py --models qwen3:8b --trials 3
.venv/bin/python scripts/run_experiment.py --methods fixed,tree,bayes,llm,hybrid --model qwen3:8b --trials 10
```

Thinking is switched off by default (`--think` turns it on). For llama.cpp, start `llama-server` and pass `--server llamacpp` (see [docs/RUNBOOK.md](docs/RUNBOOK.md)). The model choice and its reasons are in [docs/model-benchmark.md](docs/model-benchmark.md).

Optional, cloud: `--server jev --model jev-1.14.0` uses TypeSafe's Jev decision model instead of a local model, for comparison. It needs the internet and `TYPESAFE_API_KEY` set in your terminal ([docs/RUNBOOK.md](docs/RUNBOOK.md), "Jev"). Likewise `--server openrouter --model anthropic/claude-opus-5.5 --think` uses a cloud chat model through OpenRouter, with `OPENROUTER_API_KEY` ([docs/RUNBOOK.md](docs/RUNBOOK.md), "Cloud chat models through OpenRouter"), and `--server gemini --model gemini-3.8-flash --think` uses Google's free Gemini tier, with `GEMINI_API_KEY` ([docs/RUNBOOK.md](docs/RUNBOOK.md), "Gemini, free").

## With the hardware bench

Building, flashing, calibrating and running the bench is a numbered checklist in [docs/OWNER_TODO.md](docs/OWNER_TODO.md). Wiring, pin map and safety layers are in [docs/HARDWARE.md](docs/HARDWARE.md). Without a board, the whole hardware path runs against mock devices:

```bash
.venv/bin/python scripts/run_hardware.py --mock --methods bayes --scenarios healthy,F2 --trials 1
```

Mock results are labelled `sim` and go to `results/mock-serial/`.

## Layout

```
config/policy.json       costs, budget, per-session limits, load-test bounds, safety limits, timeouts
faultdiag/               the diagnosis package
  circuit.py measure.py  bench physics and what each instrument reports
  faults.py              in-model faults F1 to F5 (the engine's fault model)
  heldout.py             held-out faults H1 to H4 (only harness.py imports it)
  tools.py guard.py      tool contract and the guard every call goes through
  simbench.py            simulated bench
  serialbench.py         the real bench over USB serial (same interface)
  mockdevice.py          mock bench and injector firmware for tests
  injector.py            fault-injection controller client
  engine.py baselines.py agent.py methods.py   methods A to E
  jev.py                 optional cloud planner for D and E (TypeSafe's Jev)
  openrouter.py          optional cloud chat models for D and E (key check, model settings)
  gemini.py              optional Gemini free tier for D and E (key check, model settings)
  harness.py hwrun.py    scenarios, blind grading, summaries
  calib.py               calibration records
firmware/bench/          ESP32 bench firmware (PlatformIO); enforces every electrical limit
firmware/injector/       ESP32 fault-injection relays
gui/                     dashboard (python -m gui)
scripts/                 run_experiment, bench_models, run_hardware, calibrate, fw_selftest, injector_check, refit_noise
results/                 raw run output, append-only
docs/                    ARCHITECTURE, HARDWARE, RUNBOOK, DECISIONS, PROGRESS, OPEN_QUESTIONS, OWNER_TODO
```

## Scenarios

| Set | Scenarios |
|---|---|
| single | healthy, F1 (aged battery), F1b (marginal battery), F2 (motor connection), F3 (stall), F4 (intermittent lamp), F5 on the motor, lamp or load tap |
| compound | C1 = F1 + F3, C2 = F2 + F5 on the motor tap |
| heldout (development) | H1 (shared ground-return resistance), H2 (hidden parasitic load) |
| toolfail | TF1 = F2 with the motor tap timing out, TF2 = F3 with the motor current sensor returning garbage |
| heldout_final (frozen) | H3 (main wiring resistance), H4 (failing source current sensor): run only with `--final` |

Diagnoses are graded by circuit element, so in-model and held-out faults share one answer vocabulary.

## Rules this repository keeps

Simulation and hardware results are never mixed: every record carries `source`, and every table says which it is. H3 and H4 were frozen on 26 Oct 2026 and are not looked at before the final run. The firmware enforces every electrical limit by itself; the software and the GUI are extra layers. See [CLAUDE.md](CLAUDE.md) for the full working rules.
