"""Held-out faults: NOT in the Bayesian engine's fault model.

Only the experiment harness may import this module.
"""
from __future__ import annotations

from dataclasses import replace

from .circuit import CircuitParams
from .faults import FaultState

HELDOUT_ELEMENT = {
    "H1": "ground_return",     # corroded shared return path
    "H2": "unmonitored_load",  # hidden parasitic load on an unmonitored branch
}


def apply_heldout(params: CircuitParams, name: str, rng) -> FaultState:
    if name == "H1":
        return FaultState(replace(params, r_gnd=rng.uniform(0.8, 1.5)))
    if name == "H2":
        return FaultState(replace(params, r_parasitic=rng.uniform(5.0, 8.0)))
    raise ValueError(f"unknown held-out fault {name}")
