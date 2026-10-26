"""The hardware evaluation loop, shared by scripts/run_hardware.py and the GUI.

For each (scenario, trial) block, in a random order: the injector switches
the faults in, the truth is appended to truth.jsonl (which no method reads),
and every method runs one blind session on that same fault. Grading joins the
two files only after the run has ended.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from .harness import (FINAL_ONLY_SETS, SCENARIOS, grade, run_blind_session, scenario_table,
                      summary_table, truth_elements)
from .injector import HW_FAULTS
from .logsetup import get_logger
from .runmeta import lagos_now, run_meta
from .serialbench import BenchError, ToolFailures

log = get_logger("hwrun")


def hardware_scenarios(names: list[str] | None, final: bool) -> list[str]:
    if names is None:
        names = [n for n in SCENARIOS if n in HW_FAULTS and (final or SCENARIOS[n]["set"] not in FINAL_ONLY_SETS)]
    bad = [n for n in names if n not in HW_FAULTS]
    if bad:
        raise ValueError(f"{bad} cannot be run on hardware (no injector mapping)")
    frozen = [n for n in names if SCENARIOS[n]["set"] in FINAL_ONLY_SETS]
    if frozen and not final:
        raise ValueError(f"{frozen} are frozen final held-out scenarios; pass --final only for the final run")
    return names


def require_self_test(bench):
    """No session (and so no load test) before the firmware self-test passes on this boot."""
    st = bench.self_test()
    if not st.get("passed"):
        failing = [k for k, v in st.get("critical", {}).items() if not v]
        bench.close()
        raise BenchError(f"firmware self-test failed ({', '.join(failing)}); no load will be energised")
    log.info("firmware self-test passed on %s", bench.firmware)
    return bench


def run_hardware(bench, open_bench, injector, methods: dict, method_meta: dict, scenarios: list[str],
                 trials: int, out: Path, source: str, policy: dict, transport: str = "serial", seed0: int = 0,
                 settle_s: float = 2.0, final: bool = False, on_progress=None) -> str:
    """bench: an open SerialBench (self-test is run here). open_bench: reconnects
    after an interrupted session. Returns the graded summary (Markdown)."""
    require_self_test(bench)
    rng = np.random.default_rng(seed0)
    blocks = [(name, t) for name in scenarios for t in range(trials)]
    base = run_meta(source, policy, transport=transport, injector_fw=(injector.hello or {}).get("fw"))
    out.mkdir(parents=True, exist_ok=True)
    (out / "meta.json").write_text(json.dumps(
        {**base, "started": lagos_now(), "firmware": bench.firmware, "calibration": bench.calibration_id,
         "methods": list(methods), "scenarios": scenarios, "trials": trials, "seed0": seed0, "final": final,
         "method_meta": method_meta, "settle_s": settle_s}, indent=2))
    k = 0
    with open(out / "sessions.jsonl", "a") as sf, open(out / "truth.jsonl", "a") as tf:
        try:
            for bi, block in enumerate(rng.permutation(len(blocks))):
                name, t = blocks[block]
                spec = SCENARIOS[name]
                seed = seed0 + 1000 * list(SCENARIOS).index(name) + t
                faults = HW_FAULTS[name]
                injector.set(faults)
                log.info("block %d/%d: faults switched in (scenario hidden until grading)", bi + 1, len(blocks))
                time.sleep(settle_s)
                order = list(methods)
                rng.shuffle(order)
                for m in order:
                    k += 1
                    sid = f"s{k:03d}"
                    tf.write(json.dumps({"session_id": sid, "scenario": name, "faults": faults, "method": m,
                                         "failures": spec.get("failures", {}), "truth": truth_elements(spec),
                                         "seed": seed}) + "\n")
                    tf.flush()
                    if bench.interrupted:
                        bench.close()
                        bench = require_self_test(open_bench())
                    if sorted(injector.status()) != sorted(faults):
                        log.error("injector lost its faults; switching them in again")
                        injector.set(faults)
                        time.sleep(settle_s)
                    target = (ToolFailures(bench, spec["failures"], np.random.default_rng(seed + 10_000))
                              if spec.get("failures") else bench)
                    meta = {**base, "model": None, "model_version": None, **method_meta.get(m, {}),
                            "firmware": bench.firmware, "calibration": bench.calibration_id}
                    rec = run_blind_session(m, methods[m], seed, policy, meta, target, sid)
                    if injector.reset_seen:
                        injector.reset_seen = False
                        rec["status"], rec["error"] = "interrupted", "injector reset during the session"
                        injector.set(faults)
                    sf.write(json.dumps(rec, default=str) + "\n")
                    sf.flush()
                    if on_progress:
                        on_progress(rec)
                injector.clear()
        finally:
            try:
                injector.clear()
            except Exception as exc:  # noqa: BLE001 - it clears itself after its host timeout
                log.error("could not clear the injector: %s", exc)
            bench.close()
    return grade_dir(out)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def grade_dir(out: Path) -> str:
    """Join sessions with the truth file, write graded.jsonl and summary.md."""
    graded = grade(read_jsonl(out / "sessions.jsonl"), read_jsonl(out / "truth.jsonl"))
    (out / "graded.jsonl").write_text("".join(json.dumps(g, default=str) + "\n" for g in graded))
    valid = [g for g in graded if g["status"] != "interrupted"]
    summary = summary_table(valid) if valid else "No completed sessions.\n"
    summary += f"\nInterrupted sessions (excluded above): {len(graded) - len(valid)}\n"
    if valid:
        summary += "\nPer scenario\n\n" + scenario_table(valid)
    (out / "summary.md").write_text(summary)
    return summary
