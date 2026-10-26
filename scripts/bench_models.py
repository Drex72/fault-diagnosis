"""Benchmark local models as the hybrid agent's planner. Run on the demo laptop.

    ollama pull qwen2.5:7b
    python scripts/bench_models.py --models qwen2.5:7b,llama3.2:3b

Reports, per model: diagnosis accuracy on a fixed scenario set, invalid
tool-call rate, engine fallbacks and per-call latency. Check memory use
separately with `ollama ps` while it runs.
"""
import argparse
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from faultdiag.agent import Agent, OpenAICompatLLM  # noqa: E402
from faultdiag.engine import BayesEngine  # noqa: E402
from faultdiag.guard import load_policy  # noqa: E402
from faultdiag.harness import run_trial  # noqa: E402

DEFAULT_SCENARIOS = "F2,C1,C2,H1,H2,TF1,TF2"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    ap.add_argument("--base-url", default="http://localhost:11434/v1")
    ap.add_argument("--scenarios", default=DEFAULT_SCENARIOS)
    ap.add_argument("--trials", type=int, default=2)
    args = ap.parse_args()

    engine, policy = BayesEngine(), load_policy()
    rows = []
    for model in args.models.split(","):
        agent = Agent(OpenAICompatLLM(args.base_url, model), engine)
        recs = [run_trial(s, f"hybrid:{model}", agent.run, 5000 + t, policy)
                for s in args.scenarios.split(",") for t in range(args.trials)]
        stats = [r["agent_stats"] for r in recs if r["agent_stats"]]
        lat = [x for s in stats for x in s["latency_s"]]
        calls = sum(s["llm_calls"] for s in stats) or 1
        rows.append((model, sum(r["correct"] for r in recs) / len(recs),
                     sum(s["invalid"] for s in stats) / calls, sum(s["fallbacks"] for s in stats),
                     statistics.median(lat) if lat else float("nan"),
                     sorted(lat)[int(0.95 * (len(lat) - 1))] if lat else float("nan"),
                     sum(r["error"] is not None for r in recs)))

    print("\n| Model | Accuracy | Invalid call rate | Fallbacks | Median latency (s) | p95 latency (s) | Crashes |")
    print("|---|---|---|---|---|---|---|")
    for m, acc, inv, fb, med, p95, crash in rows:
        print(f"| {m} | {acc:.0%} | {inv:.0%} | {fb} | {med:.1f} | {p95:.1f} | {crash} |")


if __name__ == "__main__":
    main()
