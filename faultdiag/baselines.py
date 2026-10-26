"""Baselines A (fixed sequence) and B (decision tree).

Thresholds are set from the healthy-bench model; they are the kind a
technician would write down.
"""
from __future__ import annotations

KVL_MARGIN = 0.3     # V: a downstream tap above the bus is impossible
CONN_DROP = 0.3      # V: bus-to-motor drop that indicates a bad connection
STALL_I = 1.5        # A: motor current that indicates stall
LAMP_OPEN_V = 2.0    # V: lamp tap this low means the lamp connection is open
RINT_MAX = 0.06      # ohm: battery ohmic resistance above this is degraded


def _read(guard, tool, **args):
    r = guard.call(tool, args)
    if r["status"] != "ok":
        return None
    d = r["data"]
    return d.get("volts", d.get("amps", d))


def _r_int(pulse: dict | None):
    if not pulse:
        return None
    di = pulse["i_src_during"] - pulse["i_src_before"]
    return (pulse["v_src_before"] - pulse["v_src_immediate"]) / di if di > 0.1 else None


def run_fixed(guard, **_) -> dict:
    """A: read everything once, one pulse test, then apply every rule."""
    v = {p: _read(guard, "read_voltage", point=p) for p in ("src", "bus", "motor", "lamp", "load")}
    i_motor = _read(guard, "read_current", channel="motor")
    _read(guard, "read_current", channel="src")
    r_int = _r_int(_read(guard, "load_test", mode="pulse"))

    found = set()
    bus = v["bus"]
    if bus is not None:
        for tap in ("motor", "lamp", "load"):
            if v[tap] is not None and v[tap] > bus + KVL_MARGIN:
                found.add(f"sensor_{tap}")
        if v["motor"] is not None and "sensor_motor" not in found and bus - v["motor"] > CONN_DROP:
            found.add("motor_connection")
        if v["lamp"] is not None and v["lamp"] < LAMP_OPEN_V:
            found.add("lamp_connection")
    if i_motor is not None and i_motor > STALL_I:
        found.add("motor")
    if r_int is not None and r_int > RINT_MAX:
        found.add("battery")
    return {"method": "fixed", "status": "diagnosed", "elements": sorted(found)}


def run_tree(guard, **_) -> dict:
    """B: sequential tree, stops at the first fault found (single fault)."""
    def done(element):
        return {"method": "tree", "status": "diagnosed", "elements": [element] if element else []}

    bus = _read(guard, "read_voltage", point="bus")
    motor = _read(guard, "read_voltage", point="motor")
    if bus is not None and motor is not None:
        if motor > bus + KVL_MARGIN:
            return done("sensor_motor")
        if bus - motor > CONN_DROP:
            return done("motor_connection")
    i_motor = _read(guard, "read_current", channel="motor")
    if i_motor is not None and i_motor > STALL_I:
        return done("motor")
    lamp = _read(guard, "read_voltage", point="lamp")
    if lamp is not None and bus is not None:
        if lamp > bus + KVL_MARGIN:
            return done("sensor_lamp")
        if lamp < LAMP_OPEN_V:
            return done("lamp_connection")
    load = _read(guard, "read_voltage", point="load")
    if load is not None and bus is not None and load > bus + KVL_MARGIN:
        return done("sensor_load")
    r_int = _r_int(_read(guard, "load_test", mode="pulse"))
    if r_int is not None and r_int > RINT_MAX:
        return done("battery")
    return done(None)
