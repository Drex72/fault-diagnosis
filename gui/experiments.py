"""Read-only views over results/ for the Experiments, Models and Calibration pages.

Summaries are computed by the same functions the CLI uses (harness.summarise,
summary_table, hwrun.graded_summary), so the GUI shows the CLI's numbers.
Sim and hardware are never combined: every view is per run, and a run has
one source.
"""
from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path

import numpy as np

from faultdiag import calib
from faultdiag.harness import SCENARIOS, summarise, summary_table
from faultdiag.hwrun import graded_summary, read_jsonl

SKIP = ("model-bench",)  # benchmark sessions belong to the Models page


def _clean(x):
    if isinstance(x, float) and math.isnan(x):
        return None
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    return x


def run_dirs(results: Path) -> list[Path]:
    dirs = [p.parent for p in results.glob("**/sessions.jsonl")]
    return sorted((d for d in dirs if d.relative_to(results).parts[0] not in SKIP),
                  key=lambda d: d.name, reverse=True)


def run_path(results: Path, run_id: str) -> Path:
    p = (results / run_id).resolve()
    if results.resolve() not in p.parents or not (p / "sessions.jsonl").exists():
        raise LookupError(f"no run {run_id}")
    return p


def kind_of(results: Path, d: Path) -> str:
    top = d.relative_to(results).parts[0]
    return {"hardware": "hardware", "mock-serial": "mock serial", "live": "GUI sessions"}.get(top, "experiment")


def records(results: Path, d: Path) -> list[dict]:
    """Graded records for hardware runs (none until graded), raw records otherwise."""
    if kind_of(results, d) == "hardware" or (d / "truth.jsonl").exists():
        return read_jsonl(d / "graded.jsonl")
    return read_jsonl(d / "sessions.jsonl")


def run_info(results: Path, d: Path) -> dict:
    meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
    recs = records(results, d)
    raw = read_jsonl(d / "sessions.jsonl") if not recs else recs
    sources = sorted({r.get("source", "sim") for r in raw}) or [meta.get("source", "sim")]
    models = sorted({r.get("model") for r in raw if r.get("model")})
    return {"id": str(d.relative_to(results)), "kind": kind_of(results, d),
            "source": sources[0] if len(sources) == 1 else "mixed",
            "legacy": any("source" not in r for r in raw),
            "date": meta.get("started") or (raw[0].get("timestamp") if raw else None),
            "commit": meta.get("commit") or (raw[0].get("commit") if raw else None),
            "policy_hash": meta.get("policy_hash") or (raw[0].get("policy_hash") if raw else None),
            "models": models, "methods": sorted({r["method"] for r in raw}),
            "sessions": len(raw), "graded": bool(recs) or kind_of(results, d) != "hardware",
            "final": meta.get("final", False)}


def list_runs(results: Path) -> list[dict]:
    return [run_info(results, d) for d in run_dirs(results)]


def summary(results: Path, run_id: str) -> dict:
    d = run_path(results, run_id)
    recs = records(results, d)
    if not recs:
        return {"run": run_info(results, d), "rows": [], "markdown": "Not graded yet.\n", "scenarios": [],
                "cost_accuracy": []}
    hardware_style = (d / "truth.jsonl").exists()
    valid = [r for r in recs if r["status"] != "interrupted"] if hardware_style else recs
    markdown = graded_summary(recs) if hardware_style else summary_table(recs)
    rows = summarise(valid)
    methods = sorted({r["method"] for r in valid})
    order = {n: i for i, n in enumerate(SCENARIOS)}
    scen = []
    for name in sorted({r["scenario"] for r in valid}, key=lambda n: order.get(n, 999)):
        cells = {}
        for m in methods:
            rs = [r for r in valid if r["scenario"] == name and r["method"] == m]
            if rs:
                cells[m] = {"correct": sum(r["correct"] for r in rs), "n": len(rs),
                            "mean_cost": float(np.mean([r["cost"] for r in rs]))}
        scen.append({"scenario": name, "set": SCENARIOS.get(name, {}).get("set"), "cells": cells})
    by_method = {}
    for r in valid:
        by_method.setdefault(r["method"], []).append(r)
    cost_acc = [{"method": m, "accuracy": sum(r["correct"] for r in rs) / len(rs),
                 "mean_cost": float(np.mean([r["cost"] for r in rs])), "n": len(rs)} for m, rs in sorted(by_method.items())]
    return _clean({"run": run_info(results, d), "rows": rows, "markdown": markdown, "methods": methods,
                   "scenarios": scen, "cost_accuracy": cost_acc})


def session_index(results: Path, run_id: str) -> list[dict]:
    d = run_path(results, run_id)
    recs = records(results, d) or read_jsonl(d / "sessions.jsonl")
    return [{"index": i, "method": r["method"], "scenario": r.get("scenario"), "status": r["status"],
             "correct": r.get("correct"), "cost": r["cost"], "elements": r.get("elements"),
             "truth": r.get("truth"), "seed": r.get("seed"), "timestamp": r.get("timestamp")}
            for i, r in enumerate(recs)]


def session(results: Path, run_id: str, index: int) -> dict:
    d = run_path(results, run_id)
    recs = records(results, d) or read_jsonl(d / "sessions.jsonl")
    if not 0 <= index < len(recs):
        raise LookupError(f"no session {index} in {run_id}")
    return _clean(recs[index])


CSV_FIELDS = ("run", "source", "timestamp", "method", "model", "scenario", "set", "seed", "status", "elements",
              "truth", "correct", "cost", "actions", "rejections", "commit", "policy_hash", "model_version",
              "firmware", "calibration")


def to_csv(results: Path, run_id: str) -> str:
    d = run_path(results, run_id)
    buf = io.StringIO()
    w = csv.DictWriter(buf, CSV_FIELDS, extrasaction="ignore")
    w.writeheader()
    for r in records(results, d) or read_jsonl(d / "sessions.jsonl"):
        row = {**r, "run": run_id, "source": r.get("source", "sim")}
        for k in ("elements", "truth"):
            row[k] = " ".join(row.get(k) or [])
        w.writerow(row)
    return buf.getvalue()


def models(results: Path) -> dict:
    rows = []
    for f in sorted((results / "model-bench").glob("*/summary.json")):
        for r in json.loads(f.read_text()):
            rows.append({**r, "run": f.parent.name})
    latest = {}
    for r in rows:  # sorted by run, so the last one wins
        latest[r["model"]] = r
    history = {}
    for r in rows:
        history.setdefault(r["model"], []).append(
            {k: r.get(k) for k in ("run", "date", "median_latency_s", "p95_latency_s", "invalid_rate", "accuracy",
                                   "sessions")})
    return _clean({"source": "sim", "latest": list(latest.values()), "history": history})


def calibration(cal_dir: Path, mock_dir: Path | None = None) -> dict:
    hw = calib.all_records(cal_dir, "hardware")
    latest = hw[-1] if hw else None
    warning = None
    if latest is None:
        warning = "No hardware calibration yet: run scripts/calibrate.py (docs/OWNER_TODO.md step 17)."
    elif calib.age_days(latest) > calib.STALE_DAYS:
        warning = f"Calibration {latest['id']} is {calib.age_days(latest):.0f} days old: recalibrate."
    sim = calib.all_records(mock_dir, "sim") if mock_dir else []
    return {"hardware": hw, "latest": latest, "warning": warning, "stale_days": calib.STALE_DAYS,
            "mock": sim[-3:]}
