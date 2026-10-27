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
| jev-1.14.0 (cloud, added 27 Oct) | 8 | 38% | 0.0% | 0 | 0 | 0 | 0 | 0.67 | 1.64 | cloud | 11.4 |

Per scenario (correct of 3):

| Planner | F2 | C1 | C2 | H1 | H2 | TF1 | TF2 |
|---|---|---|---|---|---|---|---|
| Engine alone (C) | 3 | 3 | 2 | 0 | 0 | 0 | 3 |
| qwen3:8b | 2 | 2 | 0 | 0 | 0 | 1 | 2 |
| bonsai-8b | 0 | 0 | 1 | 0 | 0 | 1 | 2 |
| llama3.2:3b | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| jev-1.14.0 | 3 | 3 | 2 | 0 | 0 | 0 | 0 |

**Jev, added on 27 Oct 2026 after Phase 2** (`results/model-bench/20261027-103049`, commit `39143d5`, log `results/jev-bench.log`). Not a local model: TypeSafe's cloud decision model, run at the owner's request as a comparison only (`docs/DECISIONS.md`). It does not change the choice of qwen3:8b for the offline demo. Same sessions and seeds as the table above; 138 requests and 549,431 input tokens (from Jev's replies); no memory figure, because it does not run on this laptop. It matched the engine on F2, C1 and C2 and scored nothing on H1, H2, TF1 and TF2. On H2 it named `unmonitored_load` in 2 of 3 sessions but each time added a second, wrong element; on TF2 it named the broken `current_sensor_motor` every time, twice with the stall. The full comparison is in `docs/PROGRESS.md` ("After Phase 2: Jev").

What the table shows:

- No model got H1 or H2 (the development held-out faults) right, and neither does the engine. TF1 (motor tap timing out) is the one scenario where a model scored and the engine did not: qwen3:8b and Bonsai each got 1 of 3, the engine 0 of 3.
- The models spend more than twice the engine's measurement cost (about 16 against 7, of a budget of 40).
- llama3.2:3b mostly fails at the format: 190 of its 461 replies were invalid tool calls (wrong argument types, invented tools), so the engine's suggestion was used 35 times, and 8 of its sessions ended on loop detection.
- Bonsai-8B never produced an invalid call but chose poorly. In 2 of its 17 submitted diagnoses the evidence quoted a current that matches no reading taken (flagged by the evidence check); qwen3:8b and llama3.2:3b had none flagged.

## Free cloud models (27 Oct 2026; a screen, not the benchmark)

The owner has no money for credits, so the free tiers of Google's Gemini API and OpenRouter were tried, with the owner's keys. Their daily limits are small: Gemini allows 20 requests per day per model; OpenRouter allows 50 requests per day to free models in all, and failed attempts count. The 21-session benchmark needs about 150 to 230 requests, so it was not run on any of them today. This screen answers a smaller question: which free models can work as the hybrid's planner at all. All `source: sim`.

**Step 1, one request each.** The agent's exact first turn on F2 (seed 5000) was sent to each free OpenRouter model that lists tool support, and the reply was checked with the tool contract (a throwaway script, `or_probe.py`, not in the repository). 14 of the 16 were tried; left out were `openrouter/free`, which sends each request to a random model, and the 2.6B `liquid/lfm-2.5-2.6b`, the size class where llama3.2:3b failed the format. Reasoning was off where the model allows it, as for the local models.

| Model | First turn |
|---|---|
| nvidia/nemotron-3-ultra-550b-a55b:free | valid call, 1.4 s |
| nvidia/nemotron-3.5-super-vl:free | valid call, 8.6 s |
| nvidia/nemotron-3.5-lightning:free | valid call, 0.9 s |
| poolside/laguna-s-2.1:free | valid call, 2.6 s |
| cohere/north-mini-code:free | valid call, 1.1 s |
| recursive/flint-v1.0:free (always reasons) | valid call, 1.4 s |
| dots-studio/dots-3-note-preview:free | no tool call: it described the step in plain text |
| google/gemma-4-31b-it:free, google/gemma-4-26b-a4b-it:free, xingchen-agi/xing4.0-29b-a4b:free, poolside/laguna-xs-2.1:free | HTTP 429: rate-limited by their provider |
| nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free | provider overloaded (HTTP 502) |
| thinkingmachines/inkling:free, thinkingmachines/inkling-small:free | HTTP 403: "only available on agentic harnesses" |

**Step 2, one full hybrid session on F2, seed 5000**, for the four largest models that passed step 1, plus Gemini (`bench_models.py --scenarios F2 --trials 1`; OpenRouter at 15 requests a minute). The same session for the earlier planners is shown for comparison.

| Planner | Run (`results/model-bench/`) | F2, seed 5000 | Cost (of 40) | Model calls | Median reply (s) |
|---|---|---|---|---|---|
| Engine alone (C) | `20261026-213536` | correct | 8 | n/a | n/a |
| qwen3:8b | `20261026-194448` | wrong | 26 | 20 | |
| jev-1.14.0 | `20261027-103049` | correct | 13 | 5 | |
| poolside/laguna-s-2.1:free (118B, 8B active) | `20261027-141027` | correct | 8 | 7 | 5.36 |
| nvidia/nemotron-3.5-super-vl:free (120B, 12B active) | `20261027-141143` | correct | 30 | 11 | 1.38 |
| gemini-3.6-flash (Gemini API, reasoning on) | `20261027-135344` | correct; the second session then hit the daily cap | 20 | 10 | 3.69 |
| nvidia/nemotron-3-ultra-550b-a55b:free | `20261027-135706` | no session finished: the provider timed out (HTTP 504, about 2 minutes each time) for 13 minutes | | | |
| recursive/flint-v1.0:free | `20261027-141229` | no session finished: rate-limited by its provider | | | |

What it shows:

- Two free OpenRouter models, Laguna S 2.1 and Nemotron 3.5 Super VL, and Gemini 3.6 Flash can run the agent end to end: every call valid, a diagnosis submitted, and the right one. Laguna S 2.1 got it at the engine's own cost (8) with 7 calls.
- F2 is the easiest scenario (the engine gets all three seeds), so one correct F2 session says the model can do the job's format, not that it beats the engine. That needs the 21 sessions, and above all H1, H2, TF1 and C2.
- Free models are often unavailable: 7 of the 14 could not answer at the time (rate limits, overload, refusals), and the strongest-looking one, Nemotron 3 Ultra, never finished a session. Laguna S 2.1's free listing ends on 31 Oct 2026 (OpenRouter's `expiration_date`).
- Gemini 3.8 and 3.7 Flash were overloaded all afternoon (HTTP 503 "currently experiencing high demand"), so 3.6 Flash was used.

Runs at commit `76a03ac`. The two OpenRouter runs record `76a03ac+dirty`: during them only `docs/RUNBOOK.md`, `docs/OWNER_TODO.md` and the tracker seed list (`gui/seed.py`) were edited; no code they ran changed. Logs: kept outside the repository (scratch), the records are in the run folders.

**Full benchmark on Laguna S 2.1 (in progress).** Started 27 Oct 2026 in `results/model-bench/20261027-144018`: the 21 sessions above, at most 15 requests a minute. The key had 10 of its 50 free requests left that day (the screening used the rest), so the run goes on once or twice a day with `--resume` (a scheduled task in the Claude app, 09:00 and 16:00 Lagos time, that first checks the key's remaining free requests). The first start at 14:40 was stopped after a few seconds with no session recorded, because it had no `--rpm`; it was resumed in the same folder with `--rpm 15`. At about 10 requests a session it needs about four days of quota, and the free listing ends on 31 Oct 2026, so it may finish only in part; it is written up here either way.

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
