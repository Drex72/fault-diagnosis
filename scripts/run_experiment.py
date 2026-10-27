"""Run the comparison in simulation.

    python scripts/run_experiment.py --trials 10
    python scripts/run_experiment.py --methods fixed,tree,bayes,llm,hybrid --model qwen3:8b --trials 10 --final
    python scripts/run_experiment.py <same options> --resume results/<timestamp>   # continue a stopped run
    python scripts/run_experiment.py --methods llm,hybrid --server jev --model jev-1.14.0 --trials 10   # cloud, needs TYPESAFE_API_KEY
    python scripts/run_experiment.py --methods llm,hybrid --server openrouter --model anthropic/claude-opus-5.5 \
        --think --trials 10   # cloud, needs OPENROUTER_API_KEY

Results go to results/<timestamp>/ (summary.md and sessions.jsonl); FAULTDIAG_RESULTS_DIR
replaces results/ (the tests use it).
Simulation results are for development only; report them separately from hardware runs.
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from faultdiag.harness import SCENARIOS, dev_scenarios, run_all, summary_table  # noqa: E402
from faultdiag.methods import SERVERS, build_methods  # noqa: E402
from faultdiag.runmeta import LAGOS  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", default="fixed,tree,bayes")
    ap.add_argument("--scenarios", default=None,
                    help="default: every development scenario, or every scenario with --final")
    ap.add_argument("--final", action="store_true",
                    help="final evaluation: also run the frozen held-out set (H3, H4)")
    ap.add_argument("--trials", type=int, default=5)
    ap.add_argument("--server", choices=SERVERS, default="ollama",
                    help="jev: TypeSafe's cloud decision model (internet and TYPESAFE_API_KEY needed); "
                         "openrouter: cloud chat models such as anthropic/claude-opus-5.5 (OPENROUTER_API_KEY)")
    ap.add_argument("--base-url", default=None,
                    help="default: Ollama on :11434, llama.cpp on :8080, Jev at TypeSafe, OpenRouter")
    ap.add_argument("--model", default=None, help="model name (e.g. qwen3:8b, or jev-1.14.0), needed for llm and hybrid")
    ap.add_argument("--model-version", default=None, help="for llama.cpp: e.g. sha256 prefix of the GGUF")
    ap.add_argument("--think", action="store_true", help="leave a thinking model's reasoning on")
    ap.add_argument("--timeout", type=float, default=180, help="seconds per model reply")
    ap.add_argument("--resume", default=None, metavar="RUN_DIR",
                    help="continue a stopped run in RUN_DIR with the same options: recorded sessions are kept")
    args = ap.parse_args()

    try:
        methods, method_meta = build_methods(args.methods.split(","), args.model, args.server, args.base_url,
                                             args.think, args.timeout, args.model_version)
    except ValueError as exc:
        sys.exit(str(exc))

    out = Path(os.environ.get("FAULTDIAG_RESULTS_DIR", ROOT / "results")) / datetime.now(LAGOS).strftime("%Y%m%d-%H%M%S")
    scenarios = args.scenarios.split(",") if args.scenarios else (list(SCENARIOS) if args.final else dev_scenarios())
    if args.resume:
        out = Path(args.resume)
        try:
            first = json.loads((out / "meta.json").read_text())
        except (OSError, ValueError) as exc:
            sys.exit(f"cannot resume {out}: {exc}")
        now = {"methods": list(methods), "scenarios": scenarios, "trials": args.trials, "final": args.final,
               "models": {m: v.get("model") for m, v in method_meta.items()}}
        then = {k: first.get(k) for k in ("methods", "scenarios", "trials", "final")}
        then["models"] = {m: v.get("model") for m, v in (first.get("method_meta") or {}).items()}
        if now != then:
            sys.exit(f"cannot resume {out}: options differ from the original run\n  original: {then}\n  now:      {now}")
    records = run_all(methods, scenarios, args.trials, out, method_meta=method_meta, final=args.final,
                      resume=bool(args.resume))
    print("\nSIMULATION RESULTS (not hardware)\n")
    print(summary_table(records))
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()
