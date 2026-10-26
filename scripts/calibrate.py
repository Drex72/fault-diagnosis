"""Calibrate the bench's voltage taps and current sensors against the handheld multimeter.

    python scripts/calibrate.py --port /dev/cu.usbserial-0001 --meter "UNI-T UT33D+"
    python scripts/calibrate.py --port ... --push-latest     # load the latest record into the board
    python scripts/calibrate.py --mock                        # simulated technician, record labelled sim

Procedure (docs/RUNBOOK.md): the battery is replaced by the lab adjustable DC
supply, set in turn to each point voltage. At each point the script averages
raw readings from the board while you type what the meter shows. A straight
line (gain, offset) is fitted per channel, saved as a dated record in
calibration/, and pushed to the board. Hardware runs refuse a board whose
calibration is not the latest record.
"""
import argparse
import os
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
RESULTS = Path(os.environ.get("FAULTDIAG_RESULTS_DIR", ROOT / "results"))
sys.path.insert(0, str(ROOT))

from faultdiag import calib  # noqa: E402
from faultdiag.faults import FaultState  # noqa: E402
from faultdiag.logsetup import get_logger  # noqa: E402
from faultdiag.mockdevice import MockBenchDevice, MockLink  # noqa: E402
from faultdiag.runmeta import LAGOS, git_commit, lagos_now  # noqa: E402
from faultdiag.serialbench import BenchError, SerialBench, SerialLink  # noqa: E402

log = get_logger("calibrate")

SETUP_VOLTS = (6.0, 10.0, 13.0)
WHERE = {
    "tap:src": "the battery-positive terminal block (src)", "tap:bus": "the bus bar after the main fuse (bus)",
    "tap:motor": "the motor + terminal (motor)", "tap:lamp": "the lamp + terminal (lamp)",
    "tap:load": "the resistive load + terminal (load)",
}
CURRENT_HOW = {
    "current:src": "the meter on its 10 A range in series with the supply + lead",
    "current:motor": ("the 4.7 ohm 25 W resistor in place of the motor (a free-running motor draws nearly the "
                      "same current at every voltage, so it gives a poor fit) and the meter on its 10 A range in "
                      "series with it"),
}


def raw_mean(bench, channel: str, samples: int) -> tuple[float, float]:
    kind, name = channel.split(":")
    xs = []
    for _ in range(samples):
        r = bench.request("read_raw", {"kind": kind, "name": name})
        if r.get("status") != "ok":
            raise BenchError(f"read_raw {channel}: {r.get('error')}")
        xs.append(r["data"]["raw"])
    return float(np.mean(xs)), float(np.std(xs, ddof=1)) if len(xs) > 1 else 0.0


def ask_float(ask, prompt: str) -> float:
    while True:
        text = ask(prompt).strip().lower().removesuffix("v").removesuffix("a").strip()
        try:
            return float(text)
        except ValueError:
            print(f"'{text}' is not a number; try again.", file=sys.stderr)


def instructions(ch: str, v: float) -> str:
    how = (f"Put the black lead on battery negative and the red lead on {WHERE[ch]}." if ch.startswith("tap")
           else f"Connect {CURRENT_HOW[ch]}.")
    return f"[{v:g} V point] {ch}: {how}"


def collect(bench, channels, setups, samples, set_supply, wait, read_meter) -> dict:
    """wait(ch, v): the technician sets up the meter. read_meter(ch, v): their reading."""
    points = {ch: [] for ch in channels}
    for v in setups:
        set_supply(v)
        for ch in channels:
            wait(ch, v)
            mean, std = raw_mean(bench, ch, samples)
            meter = read_meter(ch, v)
            points[ch].append({"setup_v": v, "raw": round(mean, 5), "raw_std": round(std, 5), "n": samples,
                               "meter": meter})
            log.info("%s @ %g V: raw %.4f (sd %.4f) meter %.4f", ch, v, mean, std, meter)
    return points


def build_record(points: dict, source: str, firmware: str, meter: str, now=None) -> dict:
    channels = {}
    for ch, pts in points.items():
        channels[ch] = {"points": pts, **calib.fit_channel(pts)}
    return {"id": calib.new_id(now), "timestamp": lagos_now() if now is None else now.isoformat(),
            "source": source, "firmware": firmware, "meter": meter, "commit": git_commit(), "channels": channels}


def push(bench, record: dict) -> None:
    r = bench.request("calibration", calib.firmware_args(record))
    if r.get("status") != "ok" or r["data"].get("id") != record["id"]:
        raise BenchError(f"the board did not accept calibration {record['id']}: {r.get('error')}")
    log.info("board now holds calibration %s", record["id"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--meter", default="handheld multimeter (model not given)")
    ap.add_argument("--channels", default=",".join(calib.CHANNELS))
    ap.add_argument("--setups", default=",".join(f"{v:g}" for v in SETUP_VOLTS), help="supply voltages")
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--cal-dir", type=Path, default=None)
    ap.add_argument("--push-latest", action="store_true", help="only load the latest record into the board")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()
    channels = args.channels.split(",")
    setups = [float(v) for v in args.setups.split(",")]

    if args.mock:
        # A board with known errors and a perfect technician: the fit must undo the errors.
        dev = MockBenchDevice(seed=5, raw_error={"tap:src": (0.98, 0.05), "tap:motor": (1.03, -0.02),
                                                 "current:src": (1.05, 0.01)})
        bench = SerialBench(MockLink(dev))
        source, cal_dir = "sim", args.cal_dir or RESULTS / "mock-serial" / "calibration"

        def set_supply(v):
            dev.set_state(FaultState(replace(dev.sim.state.params, emf=v)))

        def wait(ch, v):
            pass

        def read_meter(ch, v):
            kind, name = ch.split(":")
            if kind == "tap":
                return round(dev.sim.execute("read_voltage", {"point": name})["data"]["volts"], 3)
            return round(dev.sim.execute("read_current", {"channel": name})["data"]["amps"], 3)
    else:
        if not args.port:
            sys.exit("--port is required (or --mock); list ports with: pio device list")
        bench = SerialBench(SerialLink(args.port))
        source, cal_dir = "hardware", args.cal_dir or calib.CAL_DIR

        def set_supply(v):
            input(f"\nSet the lab supply to {v:g} V (battery disconnected, supply on the battery terminals). "
                  "Press Enter. ")

        def wait(ch, v):
            input(instructions(ch, v) + " Press Enter when the reading is steady. ")

        def read_meter(ch, v):
            unit = "V" if ch.startswith("tap") else "A"
            return ask_float(input, f"{instructions(ch, v)} Type the meter reading ({unit}): ")

    try:
        if args.push_latest:
            rec = calib.latest(cal_dir, source)
            if rec is None:
                sys.exit(f"no {source} calibration record in {cal_dir}")
            push(bench, rec)
            return
        points = collect(bench, channels, setups, args.samples, set_supply, wait, read_meter)
        record = build_record(points, source, bench.firmware, args.meter if not args.mock else "simulated",
                              now=datetime.now(LAGOS))
        path = calib.save(record, cal_dir)
        print(f"\nCalibration {record['id']} ({source}) saved to {path}")
        for ch, fit in record["channels"].items():
            print(f"  {ch:14s} gain {fit['gain']:.5f}  offset {fit['offset']:+.5f}  residual rms {fit['residual_rms']:.5f}")
        if not args.no_push:
            push(bench, record)
    finally:
        bench.close()


if __name__ == "__main__":
    main()
