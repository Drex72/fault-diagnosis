# Runbook

How to run the demo from a cold laptop, how to start a local model server, what to do after a calibration, and what to do when something goes wrong. Commands run from the repository folder.

## Offline checklist (the day before and on the day)

Nothing below needs the internet once the model is pulled. Work through it in order.

1. **Laptop on mains power.** A model run uses most of the CPU and GPU for minutes at a time.
2. **Tests pass:** `.venv/bin/python -m pytest -q` (about 40 s, no hardware or model needed).
3. **Model pulled and server running:** `ollama list` shows the chosen model (see `docs/model-benchmark.md`). If `curl -s http://localhost:11434/api/tags` fails, start the Ollama app or run `ollama serve` in its own terminal.
4. **Model warm:** `ollama run <model> "ready"` once, so the first session does not wait for the model to load. `ollama ps` should then list it.
5. **Calibration fresh** (hardware only): the dashboard's Calibration page shows no warning, or the newest file in `calibration/` is less than 30 days old. Hardware runs refuse an older one.
6. **Battery charged** (hardware only): charge it fully the day before, then disconnect the charger. Measure it at rest with the meter. The firmware refuses load tests below 11.5 V, so a flat battery shows up as refused load tests.
7. **Boards plugged in and ports known** (hardware only): `.pio-venv/bin/pio device list`. Use the labelled cables (bench, injector).
8. **Optional proof of offline:** switch Wi-Fi off before the demo. Everything below still works.

## Demo from cold

1. Start the dashboard: `.venv/bin/python -m gui --open` (http://127.0.0.1:8765). Leave the terminal visible: warnings and errors also print there.
2. **Simulation session:** on Live session choose Bench "Simulator", Method "C: Bayesian engine", Scenario "C2", and press Start session. The step table, the hypothesis chart and the result fill in as it runs. Then the same with Method "E: LLM on the engine" and the model name: the model's replies appear on the right.
3. **Results:** the Experiments page lists every run. Open the final simulation run to show the summary table (the same numbers the command line printed), the per-scenario breakdown, cost against accuracy and any single session's step log.
4. **Hardware session** (bench wired and calibrated): on Live session, Bench panel, choose "Real bench", pick the bench and injector ports, and press Connect. The firmware and calibration versions appear. Start a session with Bench "Real bench": the server picks the scenario and hides it until the end. When a human probe is needed, the yellow panel says where to put the meter leads; type the reading and press Send reading.
5. **Safety:** the red Safe state button (top right, every page) switches every load off through the firmware and latches it. Clear it with "Clear safe state" in the Bench panel once the bench is checked. Closing the dashboard also switches the loads off within 3 s (the firmware's heartbeat cutoff).

The same runs from the command line: `scripts/run_experiment.py` (simulation), `scripts/run_hardware.py` (bench; human probes are asked in the terminal), as in `docs/OWNER_TODO.md` steps 20 to 22.

## Local model servers

**Ollama** (default): `ollama serve` or the Ollama app, port 11434. Pass `--model <name>` to the scripts.

**llama.cpp** (for GGUF files such as Bonsai-8B): start the server in its own terminal, then pass `--server llamacpp` and, so the record names the exact file, `--model-version <first 12 hex digits of the GGUF's SHA-256>`:

```bash
llama-server -m models/bonsai-8b/Bonsai-8B-Q1_0.gguf --port 8080 --jinja
shasum -a 256 models/bonsai-8b/Bonsai-8B-Q1_0.gguf | cut -c1-12
.venv/bin/python scripts/bench_models.py --server llamacpp --models bonsai-8b --model-version <digits> --trials 3
```

`--jinja` makes llama-server use the model's own chat template, which carries the tool-calling format. The `models/` folder is git-ignored.

## After calibration

Run after every new calibration record (`docs/OWNER_TODO.md` step 18).

1. `.venv/bin/python scripts/refit_noise.py` prints the current simulator noise and engine sigma values next to proposed ones, and saves them to `results/noise-fit/`.
2. If a proposed value differs from the current one by more than about 20 %, change the defaults: `noise_v`, `noise_i` in `SimBench.__init__` (`faultdiag/simbench.py`) and `sigma_v`, `sigma_i` in `BayesEngine.__init__` (`faultdiag/engine.py`). Smaller differences are within run-to-run spread; leave them.
3. Record the before and after values, the calibration id and the date in `docs/DECISIONS.md` (the "Noise refit" entry says "after: not yet run" until then), and add a line to `CHANGELOG.md`.
4. Run the tests, then a fresh simulation baseline (`scripts/run_experiment.py --trials 10`). Report it as a new run: simulation numbers before and after the refit are not comparable without saying so.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `ERROR: model server not reachable` and exit code 2 | Ollama (or llama-server) is not running. Start it (see above) and check `curl http://localhost:11434/api/tags`. |
| `model ... not pulled` | `ollama pull <model>` (needs internet once). |
| Many "invalid" or "timeout" counts for D and E | The model is slow or busy. Close other heavy apps, warm the model (`ollama run <model> "ready"`), or raise `--timeout`. Timeouts count as invalid calls and E falls back to the engine. |
| Dashboard says "Server unreachable" | The `python -m gui` process stopped. Restart it. A session that was running is marked interrupted; tabs reconnect by themselves. |
| `Address already in use` when starting the dashboard | Another copy is running. Use it, stop it, or start with `--port 8766`. |
| Bench panel says "Link lost, reconnecting" | USB cable or hub problem. The host retries for 5 s. A board that reset sends a new `hello`: the session is marked interrupted. Reconnect the bench before the next session. Use a powered hub or plug straight into the laptop (`docs/OPEN_QUESTIONS.md` item 7). |
| "refusing to run: calibration ... is ... days old" | Recalibrate (`docs/OWNER_TODO.md` step 17). Only for a check that does not count, pass `--allow-stale-calibration` (or tick the box in the dashboard). |
| "firmware self-test failed (...)" | The named part is not answering (ADC, multiplexer ground channel or source INA226). Check the I2C wiring and power to the instrumentation board, then run `scripts/fw_selftest.py --port <port>`. No load is energised until it passes. |
| Load tests come back refused | The firmware is enforcing a limit: cool-down between load tests (the host waits up to 30 s), battery below 11.5 V (charge it), or the safe state is latched (Clear safe state in the dashboard, or `resume`). |
| All taps read near 0 V | Main fuse open or battery disconnected. The firmware reports it; methods should not diagnose through it. Check the fuses with the meter, battery disconnected. |
| ESP32 resets when the motor stalls | The ESP32 must be powered from USB, not the battery, with one common ground at battery negative (`docs/HARDWARE.md`). |
| `heartbeat_lost` events | The host stopped sending for 3 s (laptop asleep, process stalled). The firmware switched the loads off. Keep the laptop awake during runs. |
| Port names changed | `.pio-venv/bin/pio device list`; the labelled cables tell bench from injector. |
