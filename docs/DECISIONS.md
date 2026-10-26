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
