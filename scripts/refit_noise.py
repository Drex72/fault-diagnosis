"""Propose simulator noise and engine sigma values from calibration data.

    python scripts/refit_noise.py                      # latest hardware calibration record
    python scripts/refit_noise.py --record calibration/cal-20261101-1030.json

Simulator noise = the spread of repeated raw readings (median sample standard
deviation, scaled by the fitted gain). Engine sigma = 3 x the root-sum-square
of that noise and the calibration fit residual: the same 3:1 margin the
current defaults use (0.03 V against 0.01 V), which leaves room for model
error. The script only proposes: a person changes the defaults in
faultdiag/simbench.py and faultdiag/engine.py and records the before and
after values in docs/DECISIONS.md, because the change moves every simulated
result.
"""
import argparse
import inspect
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
RESULTS = Path(os.environ.get("FAULTDIAG_RESULTS_DIR", ROOT / "results"))
sys.path.insert(0, str(ROOT))

from faultdiag import calib  # noqa: E402
from faultdiag.engine import BayesEngine  # noqa: E402
from faultdiag.runmeta import LAGOS, lagos_now  # noqa: E402
from faultdiag.simbench import SimBench  # noqa: E402

MARGIN = 3.0


def current_values() -> dict:
    sim = inspect.signature(SimBench.__init__).parameters
    eng = inspect.signature(BayesEngine.__init__).parameters
    return {"sim_noise_v": sim["noise_v"].default, "sim_noise_i": sim["noise_i"].default,
            "engine_sigma_v": eng["sigma_v"].default, "engine_sigma_i": eng["sigma_i"].default}


def propose(record: dict) -> dict:
    out = {}
    for kind, prefix in (("v", "tap:"), ("i", "current:")):
        chans = {k: c for k, c in record["channels"].items() if k.startswith(prefix)}
        if not chans:
            continue
        noise = float(np.median([p["raw_std"] * abs(c["gain"]) for c in chans.values() for p in c["points"]]))
        resid = float(np.sqrt(np.mean([c["residual_rms"] ** 2 for c in chans.values()])))
        out[f"sim_noise_{kind}"] = round(noise, 4)
        out[f"engine_sigma_{kind}"] = round(MARGIN * float(np.hypot(noise, resid)), 4)
        out[f"calibration_residual_{kind}"] = round(resid, 4)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", type=Path, default=None)
    ap.add_argument("--cal-dir", type=Path, default=calib.CAL_DIR)
    ap.add_argument("--source", default="hardware")
    args = ap.parse_args()
    record = json.loads(args.record.read_text()) if args.record else calib.latest(args.cal_dir, args.source)
    if record is None:
        sys.exit(f"no {args.source} calibration record in {args.cal_dir}: run scripts/calibrate.py first")
    before, after = current_values(), propose(record)
    print(f"Calibration {record['id']} (source {record['source']}, {record['timestamp']})\n")
    print("| Value | Before | Proposed |\n|---|---|---|")
    for k, v in before.items():
        print(f"| {k} | {v} | {after.get(k, '-')} |")
    folder = "noise-fit" if record["source"] == "hardware" else "mock-serial/noise-fit"
    out = RESULTS / folder / f"{datetime.now(LAGOS):%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"timestamp": lagos_now(), "source": record["source"], "calibration": record["id"],
                               "before": before, "proposed": after, "margin": MARGIN}, indent=2))
    print(f"\nSaved to {out}. Apply by editing the defaults, then record before/after in docs/DECISIONS.md.")


if __name__ == "__main__":
    main()
