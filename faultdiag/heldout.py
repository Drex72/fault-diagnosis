"""Held-out faults: NOT in the Bayesian engine's fault model.

Only the experiment harness may import this module.
"""
from __future__ import annotations

from dataclasses import replace

from .circuit import CircuitParams
from .faults import FaultState

HELDOUT_ELEMENT = {
    # Development held-out: seen during development (results discussed since Oct 2026).
    "H1": "ground_return",     # corroded shared return path
    "H2": "unmonitored_load",  # hidden parasitic load on an unmonitored branch
    # Final held-out, frozen 26 Oct 2026 (docs/DECISIONS.md). No method is run on
    # these, and no prompt or threshold is tuned against them, until the final run.
    "H3": "main_wiring",          # corroded fuse holder or main wiring: resistance between battery and bus
    "H4": "current_sensor_src",   # failing INA226 on the battery output channel: reads low (gain error)
}


def apply_heldout(params: CircuitParams, name: str, rng) -> FaultState:
    if name == "H1":
        return FaultState(replace(params, r_gnd=rng.uniform(0.8, 1.5)))
    if name == "H2":
        return FaultState(replace(params, r_parasitic=rng.uniform(5.0, 8.0)))
    if name == "H3":
        return FaultState(replace(params, r_main=rng.uniform(0.3, 0.6)))
    if name == "H4":
        return FaultState(params, current_gain={"src": rng.uniform(0.45, 0.65)})
    raise ValueError(f"unknown held-out fault {name}")
