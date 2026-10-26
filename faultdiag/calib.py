"""Calibration records: fit, save, find the latest, check freshness.

A record (calibration/cal-YYYYMMDD-HHMM.json):
    {"id": "cal-20261026-1530", "timestamp": "...+01:00", "source": "hardware" | "sim",
     "firmware": "bench-0.1.0", "meter": "UNI-T UT33D+", "commit": "...",
     "channels": {"tap:src": {"points": [{"raw": 12.31, "raw_std": 0.004, "n": 20, "meter": 12.40}, ...],
                              "gain": 1.007, "offset": 0.01, "residual_rms": 0.006}, ...}}
Reported value = raw * gain + offset. Records are never edited; a new
calibration writes a new file.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np

from .runmeta import LAGOS

CAL_DIR = Path(__file__).resolve().parent.parent / "calibration"
STALE_DAYS = 30
CHANNELS = tuple(f"tap:{t}" for t in ("src", "bus", "motor", "lamp", "load")) + ("current:src", "current:motor")
GAIN_RANGE, OFFSET_RANGE = (0.5, 2.0), (-2.0, 2.0)  # the firmware refuses anything outside these


def new_id(now: datetime | None = None) -> str:
    return (now or datetime.now(LAGOS)).strftime("cal-%Y%m%d-%H%M")


def fit_channel(points: list[dict]) -> dict:
    """Least-squares line from raw readings to meter readings."""
    raw = np.array([p["raw"] for p in points], float)
    meter = np.array([p["meter"] for p in points], float)
    if len(points) < 2 or np.ptp(raw) < 1e-6:
        raise ValueError("need at least two points with different raw readings")
    gain, offset = np.polyfit(raw, meter, 1)
    resid = meter - (gain * raw + offset)
    fit = {"gain": round(float(gain), 6), "offset": round(float(offset), 6),
           "residual_rms": round(float(np.sqrt(np.mean(resid ** 2))), 6)}
    if not (GAIN_RANGE[0] <= gain <= GAIN_RANGE[1] and OFFSET_RANGE[0] <= offset <= OFFSET_RANGE[1]):
        raise ValueError(f"fit gain {gain:.4f} offset {offset:.4f} is outside the firmware's accepted range; "
                         "check the wiring and the meter readings")
    return fit


def save(record: dict, cal_dir: Path = CAL_DIR) -> Path:
    cal_dir.mkdir(parents=True, exist_ok=True)
    path = cal_dir / f"{record['id']}.json"
    if path.exists():
        raise FileExistsError(f"{path} exists; calibration records are never overwritten")
    path.write_text(json.dumps(record, indent=2))
    return path


def all_records(cal_dir: Path = CAL_DIR, source: str | None = None) -> list[dict]:
    recs = [json.loads(p.read_text()) for p in sorted(cal_dir.glob("cal-*.json"))] if cal_dir.exists() else []
    return [r for r in recs if source is None or r.get("source") == source]


def latest(cal_dir: Path = CAL_DIR, source: str = "hardware") -> dict | None:
    recs = all_records(cal_dir, source)
    return recs[-1] if recs else None


def age_days(record: dict, now: datetime | None = None) -> float:
    return ((now or datetime.now(LAGOS)) - datetime.fromisoformat(record["timestamp"])).total_seconds() / 86400


def check(firmware_cal_id: str | None, cal_dir: Path = CAL_DIR, source: str = "hardware") -> str | None:
    """Why a run must not start with this board's calibration, or None if it may."""
    rec = latest(cal_dir, source)
    if rec is None:
        return "no calibration record exists: run scripts/calibrate.py first"
    if firmware_cal_id != rec["id"]:
        return (f"the board holds calibration '{firmware_cal_id}' but the latest record is '{rec['id']}': "
                "load it with scripts/calibrate.py --push-latest")
    if age_days(rec) > STALE_DAYS:
        return f"calibration {rec['id']} is {age_days(rec):.0f} days old (limit {STALE_DAYS}): recalibrate"
    return None


def firmware_args(record: dict) -> dict:
    """Arguments for the firmware's calibration set command."""
    args = {"action": "set", "id": record["id"], "taps": {}, "currents": {}}
    for ch, fit in record["channels"].items():
        kind, name = ch.split(":")
        args["taps" if kind == "tap" else "currents"][name] = {"gain": fit["gain"], "offset": fit["offset"]}
    return args
