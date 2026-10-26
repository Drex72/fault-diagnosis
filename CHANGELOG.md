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
