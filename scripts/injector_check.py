"""Check the fault injector's relays one at a time (wiring check, docs/OWNER_TODO.md).

    python scripts/injector_check.py --port /dev/cu.usbserial-0002 [--hold 5]
    python scripts/injector_check.py --mock --hold 0

Each fault is switched in for --hold seconds, then released. Listen for the
relay and check with the meter that the expected change happens. With the
bench powered off, measure resistance across the switched element instead.
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from faultdiag.injector import InjectorClient  # noqa: E402
from faultdiag.mockdevice import MockInjectorDevice, MockLink  # noqa: E402
from faultdiag.serialbench import SerialLink  # noqa: E402

EXPECT = {
    "F1": "about 0.22 ohm appears between the battery + post and the src terminal block",
    "F2": "about 1.5 ohm appears between the bus and the motor + terminal",
    "F3": "the brake solenoid pulls in and the motor shaft locks",
    "F4": "the lamp flickers at random (about 40 % of 300 ms slots off)",
    "F5@motor": "the motor tap divider bottom leg reads about 11.8 kohm instead of 10 kohm",
    "F5@lamp": "the lamp tap divider bottom leg reads about 11.8 kohm instead of 10 kohm",
    "F5@load": "the load tap divider bottom leg reads about 11.8 kohm instead of 10 kohm",
    "H1": "about 1.0 ohm appears in the shared return, between the ret bar and battery negative",
    "H2": "the 6.8 ohm 50 W resistor is connected from the bus to the ret bar (it gets warm: keep it short)",
    "H3": "about 0.47 ohm appears between the src terminal block and the main fuse",
    "H4": "the source INA226 VIN- lead moves to the shunt midpoint (bench reads about half the source current)",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--hold", type=float, default=5.0)
    ap.add_argument("--faults", default=",".join(EXPECT))
    args = ap.parse_args()
    if args.mock:
        inj = InjectorClient(MockLink(MockInjectorDevice()))
    elif args.port:
        inj = InjectorClient(SerialLink(args.port))
    else:
        sys.exit("--port is required (or --mock); list ports with: pio device list")
    print(f"Injector {inj.hello.get('fw')}: every relay released at start")
    ok = True
    try:
        inj.clear()
        for f in args.faults.split(","):
            active = inj.set([f])
            print(f"  {f:9s} ON  -> expect: {EXPECT[f]}")
            time.sleep(args.hold)
            inj.clear()
            released = inj.status() == []
            ok = ok and active == [f] and released
            print(f"  {f:9s} OFF -> {'released' if released else 'STILL ACTIVE'}")
    finally:
        inj.close()
    print("injector protocol OK; confirm each expected change by hand" if ok else "injector check FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
