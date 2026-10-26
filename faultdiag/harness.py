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
from .runmeta import lagos_now, run_meta
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
    # test set 5: final held-out faults, frozen 26 Oct 2026. Appended last so the
    # seeds of the scenarios above are unchanged. Only run with final=True.
    "H3": {"set": "heldout_final", "heldout": "H3"},
    "H4": {"set": "heldout_final", "heldout": "H4"},
}
FINAL_ONLY_SETS = ("heldout_final",)


def dev_scenarios() -> list[str]:
    """Scenarios that may be run during development (excludes the frozen final held-out set)."""
    return [n for n, s in SCENARIOS.items() if s["set"] not in FINAL_ONLY_SETS]


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


def run_trial(name: str, method: str, fn, seed: int, policy: dict, meta: dict | None = None,
              bench=None) -> dict:
    """One blind trial. meta (source, commit, policy hash, model...) is copied
    into the record; it defaults to a simulation run."""
    meta = meta or run_meta("sim", policy)
    guard = PolicyGuard(bench if bench is not None else make_bench(name, seed), policy)
    started = lagos_now()
    t0 = time.perf_counter()
    try:
        diag = fn(guard, seed=seed)
        error = None
    except ConnectionError:
        log.error("trial %s/%s seed=%d: model server or bench unreachable, stopping the run", method, name, seed)
        raise
    except Exception as exc:  # surface the failure, keep the run going
        log.exception("trial %s/%s seed=%d crashed", method, name, seed)
        diag, error = {"status": "crashed", "elements": []}, repr(exc)
    truth = truth_elements(SCENARIOS[name])
    rec = {
        **meta, "timestamp": started,
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
            policy: dict | None = None, method_meta: dict | None = None, final: bool = False) -> list[dict]:
    """method_meta: per-method extras such as {"hybrid": {"model": ..., "model_version": ...}}.
    Records are appended to sessions.jsonl as each trial finishes, so an
    interrupted run keeps what it had. The frozen final held-out scenarios are
    refused unless final=True."""
    blocked = [n for n in scenarios if SCENARIOS[n]["set"] in FINAL_ONLY_SETS]
    if blocked and not final:
        raise ValueError(f"{blocked} are frozen final held-out scenarios; pass final=True (--final) "
                         "only for the final evaluation run")
    policy = policy or load_policy()
    base = run_meta("sim", policy)
    method_meta = method_meta or {}
    records = []
    f = None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "meta.json").write_text(json.dumps(
            {**base, "started": lagos_now(), "methods": list(methods), "scenarios": list(scenarios),
             "trials": trials, "seed0": seed0, "method_meta": method_meta, "final": final}, indent=2))
        f = open(out_dir / "sessions.jsonl", "a")
    try:
        for name in scenarios:
            for t in range(trials):
                seed = seed0 + 1000 * list(SCENARIOS).index(name) + t
                for method, fn in methods.items():
                    meta = {**base, "model": None, "model_version": None, **method_meta.get(method, {})}
                    rec = run_trial(name, method, fn, seed, policy, meta)
                    records.append(rec)
                    if f:
                        f.write(json.dumps(rec, default=str) + "\n")
                        f.flush()
    finally:
        if f:
            f.close()
    if records:
        log.info("record fields: %s", sorted(records[0]))
    if out_dir:
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
    sources = sorted({r.get("source", "sim") for r in records})
    if len(sources) > 1:
        raise ValueError(f"refusing to summarise mixed sources {sources}: report sim and hardware separately")
    lines = [f"Source: {sources[0] if sources else 'sim'}", "",
             "| Test set | Method | n | Correct | Wrong | Inconclusive | Out-of-model flag | Mean cost | Mean cost (correct) |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in summarise(records):
        lines.append(f"| {r['set']} | {r['method']} | {r['n']} | {r['accuracy']:.0%} | {r['wrong']:.0%} | "
                     f"{r['inconclusive']:.0%} | {r['out_of_model']:.0%} | {r['mean_cost']:.1f} | {r['mean_cost_correct']:.1f} |")
    return "\n".join(lines) + "\n"
