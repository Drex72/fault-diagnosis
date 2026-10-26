# Autonomous Electrical Fault Diagnosis (SIWES)

Software for a 12 V DC diagnostic testbench. A Bayesian engine and a local LLM agent choose measurements under a cost budget, diagnose faults, and are compared against fixed-sequence and decision-tree baselines. Everything runs against a circuit simulator now and against the ESP32 testbench later, through the same tool contract.

## Status

| Part | State |
|---|---|
| Circuit simulator (nodal analysis, battery polarisation, motor back-EMF and stall) | Built, tested |
| Tool contract, policy config, guard | Built, tested |
| Bayesian engine (approach C) and baselines A, B | Built, tested, run in simulation |
| Hybrid agent (E) and LLM-only agent (D) | Built; tested only with a scripted model. Needs a real local model |
| Model benchmark script | Built; not yet run (needs Ollama on the laptop) |
| ESP32 firmware and serial transport | Not started |

## Layout

```
config/policy.json      costs, per-session limits, pulse bounds, safety limits
faultdiag/circuit.py    testbench physics
faultdiag/measure.py    what each instrument reports (taps, currents, load tests)
faultdiag/faults.py     in-model faults F1 to F5 (the engine's fault model)
faultdiag/heldout.py    held-out faults H1, H2 (only the harness imports this)
faultdiag/tools.py      tool schemas and validator, shared with the LLM
faultdiag/guard.py      every call goes through here: validation, budget, logging
faultdiag/simbench.py   simulated bench with noise, intermittent faults, tool failures
faultdiag/engine.py     Bayesian active diagnosis, expected information gain per cost
faultdiag/baselines.py  approaches A and B
faultdiag/agent.py      approaches D and E, OpenAI-compatible local LLM client
faultdiag/harness.py    scenarios, blind ground truth, metrics
scripts/                run_experiment.py, bench_models.py
```

## Setup

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
```

## Run

Baselines and engine, in simulation:

```
python scripts/run_experiment.py --trials 10
```

Set `FAULTDIAG_LOG=DEBUG` to print every tool call. Results go to `results/<timestamp>/`.

With a local model (Ollama on the default port):

```
ollama pull qwen2.5:7b
python scripts/bench_models.py --models qwen2.5:7b
python scripts/run_experiment.py --methods bayes,hybrid --model qwen2.5:7b
```

For llama.cpp, start `llama-server` and pass `--base-url http://localhost:8080/v1`.

## Scenarios

| Set | Scenarios |
|---|---|
| single | healthy, F1 (aged battery), F1b (marginal battery), F2 (motor connection), F3 (stall), F4 (intermittent lamp), F5 on motor, lamp or load tap |
| compound | C1 = F1 + F3, C2 = F2 + F5 on the motor tap |
| heldout | H1 (shared ground-return resistance), H2 (hidden parasitic load) |
| toolfail | TF1 = F2 with the motor tap timing out, TF2 = F3 with the motor current sensor returning garbage |

Diagnoses are graded by circuit element, so in-model and held-out faults share one answer vocabulary.

## First simulation results (development only, not hardware)

10 trials per scenario, run 26 Oct 2026. Correct means status "diagnosed" with exactly the right elements.

| Test set | Fixed (A) | Tree (B) | Bayes (C) |
|---|---|---|---|
| single | 91% | 91% | 94% |
| compound | 50% | 0% | 80% |
| heldout | 0% | 0% | 0% (H1 flagged out-of-model 9/10; H2 confidently wrong 10/10) |
| toolfail | 45% | 45% | 55% |

What the engine cannot do, and where the hybrid has to earn its place: C2 and TF1 need a measurement the bench cannot make (the human probe across the motor connection); H1 needs the return node probed; H2 needs a current-balance argument outside the fault model.

## Notes for the hardware phase

- The INA226 needs a lower-value shunt (about 0.01 ohm) for the 3 A motor stall current and 5 A total during load tests.
- Re-fit the simulator's noise and settling values, and the engine's sigma values, to calibration data from the real bench.
- The ESP32 must enforce the safety limits itself; the guard is a second layer, not the only one.
