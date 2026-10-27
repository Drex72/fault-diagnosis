# Dashboard screenshots

One picture per dashboard page and state, for the hardware manual (`docs/manual-prompt.md` lists them as attachments). Taken on 27 Oct 2026 with the dashboard as of the commit that adds them.

Everything here is simulated: the simulator or the mock bench, both labelled `sim`. No hardware exists yet, so nothing here is a hardware result. Each session is one real run, shown to illustrate the screens, not as a result: the numbers to report are in `docs/PROGRESS.md` and `docs/model-benchmark.md`.

| File | Page | What it shows |
|---|---|---|
| `01-live-session-start.png` | Live session | Cold start: no bench connected ("Simulation only" in the header), nothing run yet. |
| `02-live-bench-connected.png` | Live session | Mock bench connected: bench and injector firmware versions, calibration "uncalibrated", green header pill. |
| `03-live-session-steps.png` | Live session | Engine (C) on the mock bench, scenario C2, seed 5000, two steps in: hypothesis chart, "Diagnosis so far", largest residuals, step table. |
| `04-live-human-probe.png` | Live session | Hybrid (E, qwen3:8b) on the mock bench, scenario H2, seed 12001: the "Measurement needed" panel asks for a meter reading from ret (red) to neg (black) and waits up to 120 s. |
| `04b-live-human-probe-panel.png` | Live session | The same panel on its own: reading box, "Send reading", and the simulated-meter button that only the mock bench offers. |
| `04c-live-after-probe.png` | Live session | The same session finished: E named lamp_connection, the truth was unmonitored_load (not correct), and the red misfit flag ("no modelled fault explains these readings") is up. |
| `05-live-session-result.png` | Live session | The engine session from 03 finished: result, ground truth, confidence, cost, residuals. |
| `06-safe-state-latched.png` | Live session | After pressing the red Safe state button: the "Safe state latched" box with "Clear safe state", the amber header pill and the confirmation toast. |
| `07-live-hybrid-model-replies.png` | Live session | Hybrid (E, qwen3:8b) on the simulator, scenario C2, seed 5000, running: each model reply and tool call in "Model replies and warnings". |
| `08-live-hybrid-result.png` | Live session | The same session finished, with the model statistics (calls, invalid calls, fallbacks, median latency). |
| `09-experiments-runs.png` | Experiments | The list of runs with kind, source, date, commit, methods, model and session count. |
| `10-experiments-run-detail.png` | Experiments | The Phase 2 final run `20261026-213554`: summary per test set and method, per-scenario breakdown, cost against accuracy, session list, Download CSV. |
| `11-experiments-session-log.png` | Experiments | Drill-down into one session (hybrid on C2): step log and record. |
| `12-models.png` | Models | Latest benchmark per model and the history of runs. |
| `13-calibration.png` | Calibration | The "no hardware calibration yet" warning and the one mock calibration opened (gain, offset, residual RMS, points per channel). |
| `14-tracker-plan.png` | Tracker | Plan tab: the 14-day plan with status and due dates. |
| `15-tracker-parts.png` | Tracker | Parts tab: bill of materials with quantity, status, supplier, price (NGN) and date. |
| `15b-tracker-lab-equipment.png` | Tracker | Lab equipment tab: items to request and their status. |
| `16-tracker-issues.png` | Tracker | Issues and risks tab. |
| `17-phone-live-session.png` | Live session | The live page at phone width (390 px). |

Notes:

- The dashboard follows the system light or dark setting; these were taken in dark mode.
- The mock bench draws its own noise seed when it connects (`gui/live.py`), as real hardware would have its own noise, so the same session seed does not repeat its readings. The engine session in 05 (C2, seed 5000) was wrong at cost 22; an earlier capture with the same settings was correct at cost 5.
- `12-models.png` was taken 1600 px wide with the page's 1240 px width limit lifted, so the whole table shows. At normal width the latest-benchmark table scrolls sideways to its Run column.
- The tracker shows the seed data from `gui/seed.py` (a fresh database), not the owner's own edits.
- The dashboard ran on a copy of `results/` and a scratch database, so the sessions made for these pictures are not in `results/`. They appear as the `live/sim-20261027` row in 09.

How they were made: `python -m gui --results <copy of results/> --db <scratch sqlite>`, driven by headless Chrome through its DevTools protocol at 1440 x 1000 CSS pixels and 1.5x scale (17 at 390 x 844 and 2x). The script clicked the same buttons a person would and answered the human probe with the simulated-meter value.
