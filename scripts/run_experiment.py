"""Run the comparison in simulation.

    python scripts/run_experiment.py --trials 10
    python scripts/run_experiment.py --methods fixed,tree,bayes,llm,hybrid --model qwen3:8b --trials 10 --final

Results go to results/<timestamp>/ (summary.md and sessions.jsonl).
Simulation results are for development only; report them separately from hardware runs.
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from faultdiag.harness import SCENARIOS, dev_scenarios, run_all, summary_table  # noqa: E402
from faultdiag.methods import build_methods  # noqa: E402
from faultdiag.runmeta import LAGOS  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", default="fixed,tree,bayes")
    ap.add_argument("--scenarios", default=None,
                    help="default: every development scenario, or every scenario with --final")
    ap.add_argument("--final", action="store_true",
                    help="final evaluation: also run the frozen held-out set (H3, H4)")
    ap.add_argument("--trials", type=int, default=5)
    ap.add_argument("--server", choices=["ollama", "llamacpp"], default="ollama")
    ap.add_argument("--base-url", default=None, help="default: Ollama on :11434, llama.cpp on :8080")
    ap.add_argument("--model", default=None, help="local model name, needed for llm and hybrid")
    ap.add_argument("--model-version", default=None, help="for llama.cpp: e.g. sha256 prefix of the GGUF")
    ap.add_argument("--think", action="store_true", help="leave a thinking model's reasoning on")
    ap.add_argument("--timeout", type=float, default=180, help="seconds per model reply")
    args = ap.parse_args()

    try:
        methods, method_meta = build_methods(args.methods.split(","), args.model, args.server, args.base_url,
                                             args.think, args.timeout, args.model_version)
    except ValueError as exc:
        sys.exit(str(exc))

    out = ROOT / "results" / datetime.now(LAGOS).strftime("%Y%m%d-%H%M%S")
    scenarios = args.scenarios.split(",") if args.scenarios else (list(SCENARIOS) if args.final else dev_scenarios())
    records = run_all(methods, scenarios, args.trials, out, method_meta=method_meta, final=args.final)
    print("\nSIMULATION RESULTS (not hardware)\n")
    print(summary_table(records))
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()
