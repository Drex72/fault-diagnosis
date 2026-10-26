"""Measurement model: what the instrumentation reports for each action.

Shared by the simulated bench (which adds noise) and the Bayesian engine
(which uses it to predict). It knows the bench topology and sensor layer, not
any fault list.
"""
from __future__ import annotations

import numpy as np

from .circuit import CircuitParams, pulse_response, solve

TAPS = ("src", "bus", "motor", "lamp", "load")       # multiplexed voltage taps
CURRENTS = ("src", "motor")                          # INA226 channels
PROBE_POINTS = TAPS + ("ret", "neg")                 # reachable with a handheld meter
ADC_FULL_SCALE = 16.38                               # 4:1 divider into ADS1115 at +/-4.096 V
DEFAULT_PULSE_MS = 100

OBS_KEYS = {
    "read_voltage": ("volts",),
    "read_current": ("amps",),
    "load_test:pulse": ("v_src_before", "i_src_before", "v_src_immediate", "v_src_end", "i_src_during"),
    "load_test:steady": ("v_src_before", "i_src_before", "v_src_during", "i_src_during"),
}


def kind(key: str) -> str:
    """'v' for voltage readings, 'i' for current readings."""
    return "v" if key == "volts" or key.startswith("v_") else "i"


def selector(tool: str, args: dict) -> str:
    return args.get("point") or args.get("channel") or args.get("mode") or ""


def obs_key(tool: str, args: dict) -> str:
    return f"load_test:{args['mode']}" if tool == "load_test" else tool


def _tap(name: str, volts: float, offsets: dict) -> float:
    return min(max(volts + offsets.get(name, 0.0), 0.0), ADC_FULL_SCALE)


def snapshot(params: CircuitParams, offsets: dict | None = None, width_ms: int = DEFAULT_PULSE_MS) -> dict:
    """Noise-free readings for every bench action, keyed by (tool, selector)."""
    offsets = offsets or {}
    base = solve(params)
    before, imm, end = pulse_response(params, width_ms / 1000.0)
    during = solve(params, test_on=True)
    out = {("read_voltage", t): np.array([_tap(t, base.v[t], offsets)]) for t in TAPS}
    out[("read_current", "src")] = np.array([base.i_src])
    out[("read_current", "motor")] = np.array([base.i_motor])
    out[("load_test", "pulse")] = np.array([
        _tap("src", before.v["src"], offsets), before.i_src,
        _tap("src", imm.v["src"], offsets), _tap("src", end.v["src"], offsets), imm.i_src,
    ])
    out[("load_test", "steady")] = np.array([
        _tap("src", base.v["src"], offsets), base.i_src,
        _tap("src", during.v["src"], offsets), during.i_src,
    ])
    return out


def observe(params: CircuitParams, offsets: dict, tool: str, args: dict) -> np.ndarray:
    width = int(args.get("width_ms", DEFAULT_PULSE_MS))
    return snapshot(params, offsets, width)[(tool, selector(tool, args))]
