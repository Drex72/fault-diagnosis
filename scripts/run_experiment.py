"""Run the comparison in simulation.

    python scripts/run_experiment.py --trials 10
    python scripts/run_experiment.py --methods fixed,tree,bayes,hybrid --model qwen2.5:7b

Results go to results/<timestamp>/ (summary.md and sessions.jsonl).
Simulation results are for development only; report them separately from hardware runs.
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from faultdiag.agent import Agent, OpenAICompatLLM  # noqa: E402
from faultdiag.baselines import run_fixed, run_tree  # noqa: E402
from faultdiag.engine import BayesEngine  # noqa: E402
from faultdiag.harness import SCENARIOS, dev_scenarios, run_all, summary_table  # noqa: E402
from faultdiag.runmeta import LAGOS, ollama_model_version  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", default="fixed,tree,bayes")
    ap.add_argument("--scenarios", default=None,
                    help="default: every development scenario, or every scenario with --final")
    ap.add_argument("--final", action="store_true",
                    help="final evaluation: also run the frozen held-out set (H3, H4)")
    ap.add_argument("--trials", type=int, default=5)
    ap.add_argument("--base-url", default="http://localhost:11434/v1")
    ap.add_argument("--model", default=None, help="local model name, needed for llm and hybrid")
    args = ap.parse_args()

    engine = BayesEngine()
    methods, method_meta = {}, {}
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
            method_meta[m] = {"model": args.model, "model_version": ollama_model_version(args.base_url, args.model)}
        else:
            sys.exit(f"unknown method {m}")

    out = ROOT / "results" / datetime.now(LAGOS).strftime("%Y%m%d-%H%M%S")
    scenarios = args.scenarios.split(",") if args.scenarios else (list(SCENARIOS) if args.final else dev_scenarios())
    records = run_all(methods, scenarios, args.trials, out, method_meta=method_meta, final=args.final)
    print("\nSIMULATION RESULTS (not hardware)\n")
    print(summary_table(records))
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()
