"""Firmware self-test on a bare board: no load is ever energised.

    python scripts/fw_selftest.py --port /dev/cu.usbserial-0001 [--watchdog]
    python scripts/fw_selftest.py --mock [--watchdog]

Checks: hello and safe state on boot, the firmware's own critical self-test,
corrupt, unknown and over-long requests, the heartbeat cutoff, the safe-state
command, and (with --watchdog) a real watchdog reset. Writes
results/fw-selftest/<timestamp>.json and exits non-zero if anything failed.
Run it on every new board before it is wired to the bench.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = Path(os.environ.get("FAULTDIAG_RESULTS_DIR", ROOT / "results"))
sys.path.insert(0, str(ROOT))

from faultdiag.mockdevice import MockBenchDevice, MockLink  # noqa: E402
from faultdiag.runmeta import LAGOS, git_commit, lagos_now  # noqa: E402
from faultdiag.serialbench import SerialBench, SerialLink  # noqa: E402


def run_checks(bench, watchdog: bool) -> dict:
    checks = {}

    def check(name, passed, detail=""):
        checks[name] = {"passed": bool(passed), "detail": detail}
        print(f"  [{'PASS' if passed else 'FAIL'}] {name} {detail}")

    hello = bench.hello or {}
    check("hello", hello.get("fw"), f"fw={hello.get('fw')} reset_reason={hello.get('reset_reason')}")
    st = bench.self_test()
    failing = [k for k, v in st["critical"].items() if not v]
    check("firmware_self_test", st["passed"], f"failing={failing} info={st.get('info')}")
    check("boot_safe_state", st["critical"].get("boot_safe_state"))

    n0 = len(bench.rejected_lines)
    with bench._wlock:
        bench.link.write_line('{"id": 9999, "tool": "read_volt')
    time.sleep(0.5)
    check("corrupt_json_rejected", len(bench.rejected_lines) > n0, str(bench.rejected_lines[n0:])[:120])
    r = bench.request("no_such_tool")
    check("unknown_tool_rejected", r.get("status") == "error" and "unknown tool" in (r.get("error") or ""),
          r.get("error") or "")
    n0 = len(bench.rejected_lines)
    with bench._wlock:
        bench.link.write_line('{"id": 9998, "tool": "info", "args": {"pad": "' + "x" * 700 + '"}}')
    time.sleep(0.5)
    check("overlong_line_rejected", len(bench.rejected_lines) > n0, str(bench.rejected_lines[n0:])[:120])
    check("still_responding", bench.info().get("status") == "ok")

    timeout_s = (bench.hello or {}).get("limits", {}).get("heartbeat_timeout_ms", 3000) / 1000
    n0 = len(bench.events)
    bench.pause_heartbeat(timeout_s + 1.5)
    time.sleep(timeout_s + 1.0)
    lost = [e for e in bench.events[n0:] if e.get("event") == "heartbeat_lost"]
    check("heartbeat_cutoff", lost and lost[0].get("test_load_on") is False, f"after {timeout_s:.1f} s")
    time.sleep(0.6)

    r = bench.safe_state()
    info = bench.info().get("data", {})
    check("safe_state_latches", r.get("status") == "ok" and info.get("inhibited") is True
          and info.get("test_load_on") is False)
    bench.resume()
    check("resume_clears", bench.info().get("data", {}).get("inhibited") is False)
    check("test_load_off_throughout", not any(e.get("test_load_on") for e in bench.events))

    if watchdog:
        n0 = len(bench.events)
        bench.self_test(watchdog=True)  # the board stops feeding the watchdog and must reset itself
        deadline = time.monotonic() + 12
        hello2 = None
        while time.monotonic() < deadline and hello2 is None:
            hello2 = next((e for e in bench.events[n0:] if e.get("event") == "hello"), None)
            time.sleep(0.2)
        check("watchdog_reset", hello2 and hello2.get("reset_reason") == "task_wdt",
              f"reset_reason={(hello2 or {}).get('reset_reason')}")
        check("safe_after_watchdog_reset", hello2 and hello2.get("boot_safe") is True)
    return checks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--watchdog", action="store_true", help="also let the watchdog reset the board")
    args = ap.parse_args()
    if args.mock:
        bench, source, port = SerialBench(MockLink(MockBenchDevice(seed=1))), "sim", "mock"
    elif args.port:
        bench, source, port = SerialBench(SerialLink(args.port)), "hardware", args.port
    else:
        sys.exit("--port is required (or --mock); list ports with: pio device list")
    print(f"Firmware self-test ({source}, {port}): no load will be energised")
    try:
        checks = run_checks(bench, args.watchdog)
    finally:
        bench.close()
    passed = all(c["passed"] for c in checks.values())
    folder = "fw-selftest" if source == "hardware" else "mock-serial/fw-selftest"
    out = RESULTS / folder / f"{datetime.now(LAGOS):%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"source": source, "timestamp": lagos_now(), "commit": git_commit(), "port": port,
                               "firmware": (bench.hello or {}).get("fw"), "passed": passed, "checks": checks},
                              indent=2))
    print(f"\n{'PASSED' if passed else 'FAILED'}: saved to {out}")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
