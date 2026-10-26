"""Experiment harness: randomised blind trials, ground truth, metrics.

This is the only module that imports heldout.py.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from .faults import ATOM_ELEMENT, apply_atoms, sample_healthy
from .guard import PolicyGuard, load_policy
from .heldout import HELDOUT_ELEMENT, apply_heldout
from .logsetup import get_logger
from .simbench import SimBench

log = get_logger("harness")

SCENARIOS = {
    # test set 1: in-model single faults
    "healthy": {"set": "single", "atoms": ()},
    "F1": {"set": "single", "atoms": ("F1",), "f1": "degraded"},
    "F1b": {"set": "single", "atoms": ("F1",), "f1": "marginal"},
    "F2": {"set": "single", "atoms": ("F2",)},
    "F3": {"set": "single", "atoms": ("F3",)},
    "F4": {"set": "single", "atoms": ("F4",)},
    "F5@motor": {"set": "single", "atoms": ("F5@motor",)},
    "F5@lamp": {"set": "single", "atoms": ("F5@lamp",)},
    "F5@load": {"set": "single", "atoms": ("F5@load",)},
    # test set 2: in-model compound faults
    "C1": {"set": "compound", "atoms": ("F1", "F3")},
    "C2": {"set": "compound", "atoms": ("F2", "F5@motor")},
    # test set 3: held-out faults
    "H1": {"set": "heldout", "heldout": "H1"},
    "H2": {"set": "heldout", "heldout": "H2"},
    # test set 4: tool failures
    "TF1": {"set": "toolfail", "atoms": ("F2",), "failures": {"read_voltage:motor": "timeout"}},
    "TF2": {"set": "toolfail", "atoms": ("F3",), "failures": {"read_current:motor": "garbage"}},
}


def truth_elements(spec: dict) -> list[str]:
    if "heldout" in spec:
        return [HELDOUT_ELEMENT[spec["heldout"]]]
    return sorted(ATOM_ELEMENT[a] for a in spec["atoms"])


def make_bench(name: str, seed: int) -> SimBench:
    spec = SCENARIOS[name]
    rng = np.random.default_rng(seed)
    base = sample_healthy(rng)
    if "heldout" in spec:
        state = apply_heldout(base, spec["heldout"], rng)
    else:
        state = apply_atoms(base, spec["atoms"], rng, f1_severity=spec.get("f1"))
    return SimBench(state, np.random.default_rng(seed + 10_000), failures=spec.get("failures"))


def run_trial(name: str, method: str, fn, seed: int, policy: dict) -> dict:
    guard = PolicyGuard(make_bench(name, seed), policy)
    t0 = time.perf_counter()
    try:
        diag = fn(guard, seed=seed)
        error = None
    except Exception as exc:  # surface the failure, keep the run going
        log.exception("trial %s/%s seed=%d crashed", method, name, seed)
        diag, error = {"status": "crashed", "elements": []}, repr(exc)
    truth = truth_elements(SCENARIOS[name])
    rec = {
        "method": method, "scenario": name, "set": SCENARIOS[name]["set"], "seed": seed,
        "truth": truth, "elements": diag.get("elements", []), "status": diag["status"],
        "correct": diag["status"] == "diagnosed" and sorted(diag.get("elements", [])) == truth,
        "cost": guard.spent, "actions": len(guard.history), "rejections": len(guard.rejections),
        "wall_s": round(time.perf_counter() - t0, 3), "error": error,
        "agent_stats": diag.get("agent_stats"), "history": guard.history,
        "diagnosis": {k: v for k, v in diag.items() if k not in ("agent_stats",)},
    }
    log.info("%-6s %-9s seed=%-3d %-12s got=%s truth=%s cost=%d %s", method, name, seed, diag["status"],
             rec["elements"], truth, guard.spent, "OK" if rec["correct"] else "WRONG")
    return rec


def run_all(methods: dict, scenarios, trials: int, out_dir: Path | None = None, seed0: int = 0,
            policy: dict | None = None) -> list[dict]:
    policy = policy or load_policy()
    records = []
    for name in scenarios:
        for t in range(trials):
            seed = seed0 + 1000 * list(SCENARIOS).index(name) + t
            for method, fn in methods.items():
                records.append(run_trial(name, method, fn, seed, policy))
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "sessions.jsonl", "w") as f:
            for r in records:
                f.write(json.dumps(r, default=str) + "\n")
        (out_dir / "summary.md").write_text(summary_table(records))
    return records


def summarise(records: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for r in records:
        groups[(r["method"], r["set"])].append(r)
    rows = []
    for (method, tset), rs in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        n = len(rs)
        rows.append({
            "set": tset, "method": method, "n": n,
            "accuracy": sum(r["correct"] for r in rs) / n,
            "wrong": sum(r["status"] == "diagnosed" and not r["correct"] for r in rs) / n,
            "inconclusive": sum(r["status"] == "inconclusive" for r in rs) / n,
            "out_of_model": sum(r["status"] == "out_of_model" for r in rs) / n,
            "mean_cost": float(np.mean([r["cost"] for r in rs])),
            "mean_cost_correct": float(np.mean([r["cost"] for r in rs if r["correct"]])) if any(r["correct"] for r in rs) else float("nan"),
        })
    return rows


def summary_table(records: list[dict]) -> str:
    lines = ["| Test set | Method | n | Correct | Wrong | Inconclusive | Out-of-model flag | Mean cost | Mean cost (correct) |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in summarise(records):
        lines.append(f"| {r['set']} | {r['method']} | {r['n']} | {r['accuracy']:.0%} | {r['wrong']:.0%} | "
                     f"{r['inconclusive']:.0%} | {r['out_of_model']:.0%} | {r['mean_cost']:.1f} | {r['mean_cost_correct']:.1f} |")
    return "\n".join(lines) + "\n"
