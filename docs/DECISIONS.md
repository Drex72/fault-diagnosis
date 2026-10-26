# Decisions

Dated decisions with reasons. Newest at the bottom of each day.

## 2026-10-26

- **Python 3.12 venv in `.venv`.** The Mac's default `python3` is 3.14; 3.12 is used for the venv and CI because every dependency we need (numpy, pyserial, PlatformIO) has mature wheels for it. The code still targets Python 3.10+.
- **Pinned dependencies.** `numpy==2.5.4`, `pytest==9.1.1` (the versions that installed and passed the 17 tests on this Mac). Pinned so a later reinstall reproduces the results.
- **PlatformIO in its own venv (`.pio-venv`, git-ignored).** PlatformIO pulls in many packages that the diagnosis code never needs; keeping it separate keeps `requirements.txt` minimal. Version 6.1.18.
- **Provenance on every record.** Each record in `sessions.jsonl` carries `source` (`sim` or `hardware`), `commit` (with `+dirty` when tracked files had uncommitted changes), `policy_hash` (first 12 hex digits of SHA-256 over the sorted policy JSON), `model`, `model_version` (Ollama digest, first 12 hex digits), `seed` and `timestamp` (ISO 8601, Africa/Lagos). Hardware records add `firmware` and `calibration`. Each run directory also has `meta.json`.
- **Records are appended as each trial finishes** (and flushed), so a run that is interrupted or crashes keeps everything up to that point.
- **`summary_table` refuses mixed sources.** It raises if sim and hardware records are passed together, so the two can never end up in one table by accident.
- **Legacy run `results/20261026-102515`** predates the provenance fields. It is a simulation run (no hardware existed). It is left unedited because `results/` is append-only; any loader treats a record with no `source` as `sim` and marks it legacy.
- **Model benchmark settings.** Thinking models (qwen3, qwen3.5) are benchmarked with reasoning switched off (`reasoning_effort: "none"` on Ollama, `enable_thinking: false` on llama.cpp). Measured on this Mac with the same first hybrid prompt: qwen3:8b took 26.7 s per call with thinking on (about 460 generated tokens) and 1.3 s with it off. At 10 to 20 calls per session and 340 LLM sessions in Phase 2, thinking on is not workable. `--think` keeps it available.
- **A model timeout counts as an invalid call and the hybrid falls back to the engine at once** (no retry: a retry would wait the full timeout again). A server that is down or refuses the request (`LLMUnavailable`) stops the whole run instead of producing a column of crashed trials.
- **Final held-out faults frozen on 26 Oct 2026** (working rule 6). H1 and H2 are now "development held-out". The final set, in `faultdiag/heldout.py`:
  - H3, element `main_wiring`: resistance between the battery terminal and the bus (corroded fuse holder or main wiring), 0.3 to 0.6 ohm against a healthy 0.01 ohm.
  - H4, element `current_sensor_src`: failing INA226 on the battery output channel, reading low with a gain of 0.45 to 0.65 (as after a calibration-register reset or a damaged shunt joint). It affects `read_current src` and the source current fields of both load tests.
  Both use elements already in the answer vocabulary, so no method gets a hint from new element names. They form the test set `heldout_final`, appended after TF2 so every earlier scenario keeps its seeds. `run_all` and `run_experiment.py` refuse them unless `--final` is given. Only bench-physics tests touch them before the final run; no method has been run on them, and no prompt or threshold has been tuned against them.
- **`FaultState.current_gain`** (new, default empty) carries a current-sensor gain error. Only the held-out module sets it; the engine's fault atoms never do, so the engine's model is unchanged.
