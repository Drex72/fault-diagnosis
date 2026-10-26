"""Benchmark local models as the hybrid agent's planner. Run on the demo laptop.

    python scripts/bench_models.py --models llama3.2:3b,qwen3:8b
    python scripts/bench_models.py --server llamacpp --base-url http://localhost:8080/v1 \
        --models bonsai-8b --model-version <sha256 prefix of the GGUF>

Reports, per model: diagnosis accuracy on a fixed scenario set, invalid
tool-call rate, replies parsed from plain text, timeouts, engine fallbacks,
per-call latency (median, p95) and peak memory. Peak memory is sampled from
Ollama's /api/ps (the data `ollama ps` prints) or, for llama.cpp, from the
resident size of the llama-server process.

Exit codes: 0 ok, 2 model server not reachable, 3 a requested model is not
available (the others are still benchmarked).
Results: results/model-bench/<timestamp>/ (sessions.jsonl, summary.json).
"""
import argparse
import json
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from faultdiag.agent import Agent, OpenAICompatLLM  # noqa: E402
from faultdiag.engine import BayesEngine  # noqa: E402
from faultdiag.guard import load_policy  # noqa: E402
from faultdiag.harness import run_trial  # noqa: E402
from faultdiag.logsetup import get_logger  # noqa: E402
from faultdiag.runmeta import LAGOS, lagos_now, ollama_model_version, run_meta  # noqa: E402

DEFAULT_SCENARIOS = "F2,C1,C2,H1,H2,TF1,TF2"
log = get_logger("bench_models")


def _get(url: str, timeout: float = 3):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def server_root(base_url: str) -> str:
    return base_url.rstrip("/").removesuffix("/v1")


def preflight(server: str, base_url: str) -> set | None:
    """Exit 2 if the server is down. For Ollama, return the set of pulled models."""
    try:
        if server == "ollama":
            return {m["name"] for m in _get(server_root(base_url) + "/api/tags")["models"]}
        _get(base_url.rstrip("/") + "/models")
        return None
    except OSError as exc:
        hint = ("start it with `ollama serve` or open the Ollama app" if server == "ollama"
                else "start llama-server (see docs/RUNBOOK.md)")
        print(f"ERROR: model server not reachable at {base_url} ({exc}); {hint}.", file=sys.stderr)
        sys.exit(2)


class PeakMemory(threading.Thread):
    """Samples the model's memory every 0.5 s until stopped. Bytes."""

    def __init__(self, server: str, base_url: str, model: str):
        super().__init__(daemon=True)
        self.server, self.root, self.model = server, server_root(base_url), model
        self.peak, self._stop_evt = 0, threading.Event()

    def sample(self) -> int:
        if self.server == "ollama":
            for m in _get(self.root + "/api/ps").get("models", []):
                if m.get("name") == self.model or m.get("model") == self.model:
                    return int(m.get("size", 0))
            return 0
        pids = subprocess.run(["pgrep", "-x", "llama-server"], capture_output=True, text=True).stdout.split()
        rss = [subprocess.run(["ps", "-o", "rss=", "-p", p], capture_output=True, text=True).stdout.strip()
               for p in pids]
        return sum(int(r) * 1024 for r in rss if r)

    def run(self):
        while not self._stop_evt.is_set():
            try:
                self.peak = max(self.peak, self.sample())
            except OSError as exc:
                log.warning("memory sample failed: %s", exc)
            self._stop_evt.wait(0.5)

    def stop(self) -> int:
        self._stop_evt.set()
        self.join(timeout=5)
        return self.peak


def unload(server: str, base_url: str, model: str):
    if server != "ollama":
        return
    body = json.dumps({"model": model, "keep_alive": 0}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(server_root(base_url) + "/api/generate", body), timeout=30).read()
    except OSError as exc:
        log.warning("could not unload %s: %s", model, exc)


def pct(xs, q):
    return sorted(xs)[int(q * (len(xs) - 1))] if xs else float("nan")


def bench_model(model, args, policy, engine, out_f) -> dict:
    extra = {}
    if not args.think:
        extra = ({"reasoning_effort": "none"} if args.server == "ollama"
                 else {"chat_template_kwargs": {"enable_thinking": False}})
    llm = OpenAICompatLLM(args.base_url, model, timeout_s=args.timeout, extra=extra)
    version = args.model_version or ollama_model_version(args.base_url, model)
    meta = {**run_meta("sim", policy), "model": model, "model_version": version, "think": args.think}

    # Warm-up: load the model before timing, so p95 is not just the load time.
    t0 = time.perf_counter()
    try:
        llm([{"role": "user", "content": "Reply with OK."}], [])
    except TimeoutError:
        log.warning("warm-up for %s timed out", model)
    load_s = round(time.perf_counter() - t0, 1)

    mem = PeakMemory(args.server, args.base_url, model)
    mem.start()
    agent = Agent(llm, engine)
    recs = []
    for s in args.scenarios.split(","):
        for t in range(args.trials):
            rec = run_trial(s, f"hybrid:{model}", agent.run, 5000 + t, policy, meta)
            recs.append(rec)
            out_f.write(json.dumps(rec, default=str) + "\n")
            out_f.flush()
    peak = mem.stop()

    stats = [r["agent_stats"] for r in recs if r["agent_stats"]]
    lat = [x for s in stats for x in s["latency_s"]]
    calls = sum(s["llm_calls"] for s in stats) or 1
    return {
        "model": model, "model_version": version, "server": args.server, "think": args.think,
        "date": lagos_now(), "commit": meta["commit"], "policy_hash": meta["policy_hash"],
        "sessions": len(recs), "llm_calls": calls,
        "accuracy": sum(r["correct"] for r in recs) / len(recs),
        "invalid_rate": sum(s["invalid"] for s in stats) / calls,
        "content_parsed": sum(s["content_parsed"] for s in stats),
        "no_call": sum(s.get("no_call", 0) for s in stats),
        "timeouts": sum(s.get("timeouts", 0) for s in stats),
        "fallbacks": sum(s["fallbacks"] for s in stats),
        "submitted": sum("evidence" in r["diagnosis"] for r in recs),
        "median_latency_s": statistics.median(lat) if lat else float("nan"),
        "p95_latency_s": pct(lat, 0.95),
        "peak_memory_gb": round(peak / 1e9, 2), "load_s": load_s,
        "crashes": sum(r["error"] is not None for r in recs),
        "per_scenario": {s: sum(r["correct"] for r in recs if r["scenario"] == s) / args.trials
                         for s in args.scenarios.split(",")},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    ap.add_argument("--server", choices=["ollama", "llamacpp"], default="ollama")
    ap.add_argument("--base-url", default=None, help="default: Ollama on :11434, llama.cpp on :8080")
    ap.add_argument("--scenarios", default=DEFAULT_SCENARIOS)
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=60, help="seconds per model reply")
    ap.add_argument("--think", action="store_true", help="leave a thinking model's reasoning on")
    ap.add_argument("--model-version", default=None, help="for llama.cpp: e.g. sha256 prefix of the GGUF")
    args = ap.parse_args()
    args.base_url = args.base_url or ("http://localhost:11434/v1" if args.server == "ollama"
                                      else "http://localhost:8080/v1")

    pulled = preflight(args.server, args.base_url)
    models, missing = [], []
    for m in args.models.split(","):
        if pulled is not None and m not in pulled and f"{m}:latest" not in pulled:
            print(f"ERROR: model {m} is not pulled on this Ollama server; run `ollama pull {m}`. Skipping it.",
                  file=sys.stderr)
            missing.append(m)
        else:
            models.append(m)

    if not models:
        sys.exit(3)
    policy, engine = load_policy(), BayesEngine()
    out = ROOT / "results" / "model-bench" / datetime.now(LAGOS).strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    with open(out / "sessions.jsonl", "a") as f:
        for m in models:
            rows.append(bench_model(m, args, policy, engine, f))
            unload(args.server, args.base_url, m)
            (out / "summary.json").write_text(json.dumps(rows, indent=2))

    print("\nMODEL BENCHMARK (simulated bench, hybrid planner)\n")
    print("| Model | Version | Accuracy | Invalid call rate | Plain-text parsed | Timeouts | Fallbacks | "
          "Median latency (s) | p95 latency (s) | Peak memory (GB) | Crashes |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['model']} | {r['model_version']} | {r['accuracy']:.0%} | {r['invalid_rate']:.1%} | "
              f"{r['content_parsed']} | {r['timeouts']} | {r['fallbacks']} | {r['median_latency_s']:.2f} | "
              f"{r['p95_latency_s']:.2f} | {r['peak_memory_gb']:.1f} | {r['crashes']} |")
    print(f"\nSaved to {out}")
    if missing:
        sys.exit(3)


if __name__ == "__main__":
    main()
