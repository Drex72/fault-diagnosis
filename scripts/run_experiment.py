"""Run the comparison in simulation.

    python scripts/run_experiment.py --trials 10
    python scripts/run_experiment.py --methods fixed,tree,bayes,hybrid --model qwen2.5:7b

Results go to results/<timestamp>/ (summary.md and sessions.jsonl).
Simulation results are for development only; report them separately from hardware runs.
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from faultdiag.agent import Agent, OpenAICompatLLM  # noqa: E402
from faultdiag.baselines import run_fixed, run_tree  # noqa: E402
from faultdiag.engine import BayesEngine  # noqa: E402
from faultdiag.harness import SCENARIOS, run_all, summary_table  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", default="fixed,tree,bayes")
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    ap.add_argument("--trials", type=int, default=5)
    ap.add_argument("--base-url", default="http://localhost:11434/v1")
    ap.add_argument("--model", default=None, help="local model name, needed for llm and hybrid")
    args = ap.parse_args()

    engine = BayesEngine()
    methods = {}
    for m in args.methods.split(","):
        if m == "fixed":
            methods[m] = run_fixed
        elif m == "tree":
            methods[m] = run_tree
        elif m == "bayes":
            methods[m] = engine.run
        elif m in ("hybrid", "llm"):
            if not args.model:
                sys.exit(f"--model is required for method {m}")
            agent = Agent(OpenAICompatLLM(args.base_url, args.model), engine if m == "hybrid" else None)
            methods[m] = agent.run
        else:
            sys.exit(f"unknown method {m}")

    out = ROOT / "results" / time.strftime("%Y%m%d-%H%M%S")
    records = run_all(methods, args.scenarios.split(","), args.trials, out)
    print("\nSIMULATION RESULTS (not hardware)\n")
    print(summary_table(records))
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()
