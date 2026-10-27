"""Run the comparison on the real bench (or, with --mock, on mock serial devices).

    python scripts/run_hardware.py --port /dev/cu.usbserial-0001 --injector-port /dev/cu.usbserial-0002 \
        --methods fixed,tree,bayes,llm,hybrid --model qwen3:8b --trials 3
    python scripts/run_hardware.py --mock --methods bayes --trials 1      # pipeline check, labelled sim
    python scripts/run_hardware.py --grade results/hardware/20261101-1000  # re-grade a finished run

Before the first session: the firmware self-test must pass and the board must
hold the latest calibration (override: --allow-stale-calibration). Faults are
switched by the injector; the truth goes to truth.jsonl, which no method reads,
and sessions are graded only after the whole run ends.
"""
import argparse
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
from faultdiag.guard import load_policy  # noqa: E402
from faultdiag.harness import state_for_faults  # noqa: E402
from faultdiag.hwrun import grade_dir, hardware_scenarios, run_hardware  # noqa: E402
from faultdiag.injector import InjectorClient  # noqa: E402
from faultdiag.logsetup import get_logger  # noqa: E402
from faultdiag.methods import SERVERS, build_methods  # noqa: E402
from faultdiag.mockdevice import MockBenchDevice, MockInjectorDevice, MockLink  # noqa: E402
from faultdiag.runmeta import LAGOS  # noqa: E402
from faultdiag.serialbench import BenchError, SerialBench, SerialLink, cli_prober  # noqa: E402

log = get_logger("run_hardware")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", help="bench ESP32 serial port (see: pio device list)")
    ap.add_argument("--injector-port", help="fault injector ESP32 serial port")
    ap.add_argument("--mock", action="store_true", help="mock serial devices; results are labelled sim")
    ap.add_argument("--grade", type=Path, help="only re-grade an existing run directory")
    ap.add_argument("--methods", default="fixed,tree,bayes")
    ap.add_argument("--scenarios", default=None, help="default: every hardware development scenario")
    ap.add_argument("--final", action="store_true", help="final evaluation: include the frozen H3, H4")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--seed0", type=int, default=0)
    ap.add_argument("--settle", type=float, default=2.0, help="seconds after switching a fault")
    ap.add_argument("--server", choices=SERVERS, default="ollama",
                    help="jev: TypeSafe's cloud decision model (internet and TYPESAFE_API_KEY needed)")
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--model-version", default=None)
    ap.add_argument("--think", action="store_true")
    ap.add_argument("--timeout", type=float, default=180)
    ap.add_argument("--allow-stale-calibration", action="store_true")
    args = ap.parse_args()

    if args.grade:
        print(grade_dir(args.grade))
        return

    policy = load_policy()
    try:
        scenarios = hardware_scenarios(args.scenarios.split(",") if args.scenarios else None, args.final)
        methods, method_meta = build_methods(args.methods.split(","), args.model, args.server, args.base_url,
                                             args.think, args.timeout, args.model_version)
    except ValueError as exc:
        sys.exit(str(exc))

    if args.mock:
        rng = np.random.default_rng(args.seed0 + 77)
        bench_dev = MockBenchDevice(seed=args.seed0, cooldown_scale=0.02)
        inj_dev = MockInjectorDevice(on_change=lambda faults: bench_dev.set_state(state_for_faults(faults, rng)))

        def prober(a, b, instruction, timeout_s):  # the simulated technician
            return bench_dev.sim.execute("human_probe", {"point_a": a, "point_b": b})["data"]["volts"]

        def open_bench():
            return SerialBench(MockLink(bench_dev), policy, prober=prober)

        injector = InjectorClient(MockLink(inj_dev))
        source, transport = "sim", "mock-serial"
    else:
        if not args.port or not args.injector_port:
            sys.exit("--port and --injector-port are required (or --mock); list ports with: pio device list")

        def open_bench():
            return SerialBench(SerialLink(args.port), policy, prober=cli_prober)

        injector = InjectorClient(SerialLink(args.injector_port))
        source, transport = "hardware", "serial"

    try:
        bench = open_bench()
    except BenchError as exc:
        injector.close()
        sys.exit(f"bench not usable: {exc}")
    if source == "hardware":
        problem = calib.check(bench.calibration_id)
        if problem and not args.allow_stale_calibration:
            bench.close()
            injector.close()
            sys.exit(f"refusing to run: {problem} (override: --allow-stale-calibration)")
        if problem:
            log.warning("running with a stale calibration (overridden): %s", problem)

    out = RESULTS / ("hardware" if source == "hardware" else "mock-serial") / \
        datetime.now(LAGOS).strftime("%Y%m%d-%H%M%S")
    try:
        summary = run_hardware(bench, open_bench, injector, methods, method_meta, scenarios, args.trials, out,
                               source, policy, transport=transport, seed0=args.seed0, settle_s=args.settle,
                               final=args.final)
    finally:
        injector.close()
    label = "HARDWARE RESULTS" if source == "hardware" else "MOCK SERIAL RESULTS (simulation, not hardware)"
    print(f"\n{label}\n\n{summary}\nSaved to {out}")
    print(json.dumps({"out": str(out)}))


if __name__ == "__main__":
    main()
