"""In-model fault atoms: the faults the Bayesian engine is told about.

Held-out faults live in heldout.py. The engine must never import that module;
tests/test_engine.py checks this.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .circuit import CircuitParams

ATOMS = ("F1", "F2", "F3", "F4", "F5@motor", "F5@lamp", "F5@load")

# Each atom maps to the circuit element it lives in. Diagnoses are graded by
# element, so in-model and held-out faults share one answer vocabulary.
ATOM_ELEMENT = {
    "F1": "battery",
    "F2": "motor_connection",
    "F3": "motor",
    "F4": "lamp_connection",
    "F5@motor": "sensor_motor",
    "F5@lamp": "sensor_lamp",
    "F5@load": "sensor_load",
}


@dataclass
class FaultState:
    params: CircuitParams
    sensor_offsets: dict = field(default_factory=dict)  # tap -> volts added
    p_lamp_open: float = 0.0                            # >0 means intermittent


def sample_healthy(rng) -> CircuitParams:
    """A healthy bench with +/-5 % component tolerance."""
    base = CircuitParams()

    def j(x):
        return x * rng.uniform(0.95, 1.05)

    return replace(
        base,
        emf=rng.uniform(12.5, 12.8),
        r_int=j(base.r_int), r_pol=j(base.r_pol), r_main=j(base.r_main),
        r_conn_motor=j(base.r_conn_motor), r_conn_lamp=j(base.r_conn_lamp),
        r_conn_load=j(base.r_conn_load), r_lamp=j(base.r_lamp), r_load=j(base.r_load),
        r_gnd=j(base.r_gnd), r_test=j(base.r_test), motor_ra=j(base.motor_ra),
        motor_k=j(base.motor_k), motor_b=j(base.motor_b), motor_torque=j(base.motor_torque),
    )


def apply_atoms(params: CircuitParams, atoms, rng, f1_severity: str | None = None) -> FaultState:
    state = FaultState(params)
    for atom in sorted(atoms):
        p = state.params
        if atom == "F1":  # aged battery: raised internal resistance
            sev = f1_severity or ("marginal" if rng.random() < 0.5 else "degraded")
            if sev == "marginal":
                state.params = replace(p, r_int=rng.uniform(0.09, 0.12), r_pol=rng.uniform(0.05, 0.07))
            else:
                state.params = replace(p, r_int=rng.uniform(0.20, 0.30), r_pol=rng.uniform(0.12, 0.20))
        elif atom == "F2":  # high-resistance motor connection
            state.params = replace(p, r_conn_motor=rng.uniform(1.2, 2.0))
        elif atom == "F3":  # motor stall
            state.params = replace(p, motor_stalled=True)
        elif atom == "F4":  # intermittent lamp connection
            state.p_lamp_open = rng.uniform(0.3, 0.5)
        elif atom.startswith("F5@"):  # sensor fault: offset on one tap
            state.sensor_offsets[atom.split("@")[1]] = rng.uniform(1.2, 2.0)
        else:
            raise ValueError(f"unknown fault atom {atom}")
    return state
