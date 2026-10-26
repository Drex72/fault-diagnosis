# Model benchmark (Phase 1)

Which local model plans for the hybrid (method E). All numbers below come from the run files named next to them; all are `source: sim` (simulated bench). Run on 26 Oct 2026.

## Result and choice

**Chosen: qwen3:8b** (Ollama digest `500a1f067a9f`), thinking off. It was the most accurate model tried (33%), with a low invalid-call rate (2.1%), a median reply of 4.7 s and 8.1 GB of memory, which leaves the laptop usable for the dashboard during a run.

**No model was reliable enough to beat the engine on its own.** On the same 21 sessions the Bayesian engine alone (method C) scores 52%; the best hybrid scores 33%. qwen3:8b is chosen because Phase 2 needs a model for methods D and E, not because it improves on C. Whether the hybrid helps anywhere on the full scenario set is what Phase 2 measures.

Final benchmark, agent at commit `30dffe4` (`results/model-bench/20261026-194448` for the Ollama models, `results/model-bench/20261026-211755` for Bonsai), engine reference at `cd32167` (`results/model-bench/20261026-213536`; the engine code is the same in both commits):

| Planner | Correct (of 21) | Accuracy | Invalid call rate | Plain-text replies parsed | Timeouts | Unparseable | Engine fallbacks | Median reply (s) | p95 reply (s) | Peak memory (GB) | Mean cost (of 40) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Engine alone (C), no model | 11 | 52% | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 7.1 |
| qwen3:8b | 7 | 33% | 2.1% | 0 | 0 | 0 | 0 | 4.74 | 8.18 | 8.1 | 16.7 |
| bonsai-8b (1-bit) | 4 | 19% | 0.0% | 0 | 0 | 0 | 0 | 3.38 | 6.81 | 15.1 | 16.2 |
| llama3.2:3b | 0 | 0% | 41.2% | 8 | 0 | 0 | 35 | 4.10 | 7.21 | 4.2 | 31.1 |

Per scenario (correct of 3):

| Planner | F2 | C1 | C2 | H1 | H2 | TF1 | TF2 |
|---|---|---|---|---|---|---|---|
| Engine alone (C) | 3 | 3 | 2 | 0 | 0 | 0 | 3 |
| qwen3:8b | 2 | 2 | 0 | 0 | 0 | 1 | 2 |
| bonsai-8b | 0 | 0 | 1 | 0 | 0 | 1 | 2 |
| llama3.2:3b | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

What the table shows:

- No model got H1 or H2 (the development held-out faults) right, and neither does the engine. TF1 (motor tap timing out) is the one scenario where a model scored and the engine did not: qwen3:8b and Bonsai each got 1 of 3, the engine 0 of 3.
- The models spend more than twice the engine's measurement cost (about 16 against 7, of a budget of 40).
- llama3.2:3b mostly fails at the format: 190 of its 461 replies were invalid tool calls (wrong argument types, invented tools), so the engine's suggestion was used 35 times, and 8 of its sessions ended on loop detection.
- Bonsai-8B never produced an invalid call but chose poorly. In 2 of its 17 submitted diagnoses the evidence quoted a current that matches no reading taken (flagged by the evidence check); qwen3:8b and llama3.2:3b had none flagged.

## Setup

| Item | Value |
|---|---|
| Machine | Apple M4 Pro, 48 GiB RAM, macOS (Darwin 25.1.0), on battery throughout |
| Ollama | 0.32.1; models loaded with a 32768-token window (`context_length` in `/api/ps`) |
| llama.cpp | `llama-server` 0.7.0 (build 11669, commit 620d2225c), `--jinja -c 16384` |
| Bonsai file | `models/bonsai-8b/Bonsai-8B-Q1_0.gguf`, 1,158,654,496 bytes, SHA-256 prefix `284a335aa3fb` |
| Planner | hybrid agent (method E): the model chooses each call, with the engine's ranked hypotheses and suggested tests in every turn |
| Scenarios | F2, C1, C2, H1, H2, TF1, TF2 (in-model, compound, development held-out, tool failure). The frozen final faults H3 and H4 were never used |
| Trials | 3 per scenario, seeds 5000, 5001, 5002, the same for every model (paired) |
| Model settings | temperature 0, thinking off (`reasoning_effort: none` on Ollama, `enable_thinking: false` on llama.cpp), 60 s per reply |
| Peak memory | Ollama: the model's allocation from `/api/ps` (what `ollama ps` shows), sampled every 0.5 s. llama.cpp: resident size of the whole `llama-server` process. The two are not measured the same way |
| Latency | per model reply, measured with Python's `perf_counter` |

Commands (from the repository folder):

```bash
.venv/bin/python scripts/bench_models.py --models llama3.2:3b,qwen3:8b --trials 3 --timeout 60
llama-server -m models/bonsai-8b/Bonsai-8B-Q1_0.gguf --port 8080 --jinja -c 16384
.venv/bin/python scripts/bench_models.py --server llamacpp --models bonsai-8b --model-version 284a335aa3fb --trials 3 --timeout 60
.venv/bin/python scripts/bench_models.py --engine-only
```

## Shortlist

Installed models (`ollama list`) and the 1-bit Bonsai file. The reasons for each are in `docs/DECISIONS.md` ("Model shortlist").

| Model | Size on disk | Benchmarked |
|---|---|---|
| llama3.2:3b | 2.0 GB | yes (3B class, tool calling) |
| qwen3:8b | 5.2 GB | yes (8B class, tool calling) |
| bonsai-8b, 1-bit GGUF | 1.2 GB | yes, on llama.cpp |
| qwen3.5:9b | 6.6 GB | partly: 9 sessions, then left out on speed (below) |
| qwen3.5:27b | 17 GB | no: too slow for Phase 2 |
| mixtral:8x7b | 26 GB | no: too large for comfortable headroom |
| Qwythos-9B (community fine-tune) | 6.7 GB | no: unclear provenance, no tool-calling evaluation |

**qwen3.5:9b** ran 9 sessions in the first run (agent before the Phase 2 changes, `results/model-bench/20261026-144203`): 6 of 9 correct (F2 3/3, C1 1/3, C2 2/3), but its median reply took 27.8 s, and 18 of its 124 replies hit the 60 s timeout. Its sessions took about 7 minutes each, so the 340 model sessions of Phase 2 would take about 40 hours. It was not rerun on the final agent code; its 9 sessions are not comparable with the table above and are reported only as the reason it was left out. To complete it: `.venv/bin/python scripts/bench_models.py --models qwen3.5:9b --trials 3 --timeout 60` (about 2.5 hours).

## How the benchmark got here

Three runs, because two problems were found and fixed along the way. They are kept because they are findings about the agent, not only about the models.

| Run | Agent code | qwen3:8b | bonsai-8b | llama3.2:3b |
|---|---|---|---|---|
| 1: `20261026-144203` | `5633da7`, before the Phase 2 agent changes | 38% (8/21), invalid 2.2%, median 5.10 s | not run | 0%, invalid 42.3%, median 4.11 s |
| 2: `20261026-165919`, `20261026-172933` | `a0d22ac` and `89fe15f`, Phase 2 agent with the first context trimming | 19% (4/21), invalid 5.1%, median 3.93 s | 14% (3/21) | 0%, invalid 27.4% |
| 3 (final): `20261026-194448`, `20261026-211755` | `30dffe4`, trimming fixed | 33% (7/21), invalid 2.1%, median 4.74 s | 19% (4/21) | 0%, invalid 41.2% |

1. **Run 1 stopped after 51 of 63 sessions.** In H1, qwen3.5:9b wrote a malformed tool call and Ollama answered HTTP 500 ("XML syntax error on line 8: element <function> closed by </parameter>"). The client treated every HTTP error as "server down" and ended the run. Fixed in `a0d22ac`: such a reply now counts as an invalid call ("Unparseable") and the hybrid falls back to the engine. None occurred in run 3.
2. **Run 1's records carry the wrong commit for two of the three models** (`84cdb7a`, `07630e1+dirty`). Commits were made while it ran and the commit was re-read per model; the process ran `5633da7` throughout. Fixed in `89fe15f` (the commit is taken once, at start).
3. **Run 2 showed the first context trimming hurt the agent.** It started at 8000 characters (about the fifth call) and then sent the whole conversation as one compacted message. qwen3:8b lost its thread and kept measuring instead of submitting: accuracy fell from 38% to 19% on the same seeds, and mean cost rose from 15.8 to 25.6. Fixed in `30dffe4`: trimming starts at 36000 characters and keeps the latest 4 turns word for word. In run 3 only 2 of qwen3:8b's sessions were long enough to be trimmed. Its 33% in run 3 against 38% in run 1 is one session out of 21, within run-to-run noise at this size, so the Phase 2 changes (loop detection, evidence check, trimming) are kept.

Smoke runs before run 1, to check the script end to end: `results/model-bench/20261026-115055` (llama3.2:3b, F2 and H1, 1 trial each) and `results/model-bench/20261026-115747` (llama3.2:3b, F2, 2 trials; stopped before its summary was written). Not used for any result.

## Caveats

- 21 sessions per model is small. A difference of one or two sessions (5 to 10 points) is noise; the gap between the engine (11 correct) and the best model (7) is larger, and Phase 2 measures it on 10 trials of every scenario.
- Run 3 paused for 42 minutes (20:14 to 20:56) when the laptop lid was closed, during llama3.2:3b's last session. Nothing was lost, and the reply times are unaffected: the timer does not run while the Mac sleeps (llama3.2:3b's longest recorded reply is 10.6 s).
- Other work ran at the same time for short periods: in run 1 the GUI tests and the smoke runs; in run 2 the test suite (about 50 s) during Bonsai; in run 3 two quick tests and the 3 s engine reference during llama3.2:3b. The latency columns may be slightly high where they overlap.
