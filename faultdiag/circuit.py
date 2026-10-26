"""DC testbench circuit model, solved by nodal analysis.

Node voltages are referenced to the battery negative terminal, which is the
instrumentation ground on the real bench. Loads return to a shared node
("ret") that reaches battery negative through the return-path resistance.

Topology:
    battery(+) --r_main-- bus --r_conn_motor-- motor --+
                           |---r_conn_lamp---- lamp ---+
                           |---r_conn_load---- load ---+-- ret --r_gnd-- battery(-)
                           |---r_parasitic (unmonitored)
                           |---r_test (switched test load)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

NODES = ("src", "bus", "motor", "lamp", "load", "ret")
_S, _B, _M, _L, _R, _G = range(6)
INF = float("inf")


@dataclass(frozen=True)
class CircuitParams:
    """Element values for the 12 V testbench. Resistances in ohms."""

    emf: float = 12.7            # battery open-circuit voltage (V)
    r_int: float = 0.03          # battery ohmic resistance
    r_pol: float = 0.02          # battery polarisation resistance (slow part)
    tau_pol: float = 2.0         # polarisation time constant (s)
    r_main: float = 0.01         # battery terminal to bus: fuse and wiring
    r_conn_motor: float = 0.05
    r_conn_lamp: float = 0.05
    r_conn_load: float = 0.05
    r_lamp: float = 14.4         # 12 V 10 W lamp, treated as linear
    r_load: float = 24.0
    r_gnd: float = 0.02          # shared return path to battery negative
    r_parasitic: float = INF     # unmonitored branch from the bus (INF = absent)
    r_test: float = 4.0          # switched test load
    motor_ra: float = 4.0        # armature resistance
    motor_k: float = 0.03        # torque and back-EMF constant
    motor_b: float = 2e-5        # viscous friction coefficient
    motor_torque: float = 0.004  # mechanical load torque (N m)
    motor_stalled: bool = False  # rotor mechanically locked
    lamp_open: bool = False      # lamp connection open at this instant


@dataclass(frozen=True)
class Solution:
    v: dict              # node name -> volts, plus "neg" = 0
    i_src: float         # battery output current
    i_motor: float
    i_lamp: float
    i_load: float
    i_parasitic: float
    i_test: float
    motor_speed: float   # rad/s
    stalled: bool


def solve(p: CircuitParams, test_on: bool = False, v_pol: float | None = None) -> Solution:
    """Steady-state solution. With v_pol given, the polarisation voltage is
    held fixed (used for the instant after a load step)."""
    for stalled in ((True,) if p.motor_stalled else (False, True)):
        Y = np.zeros((6, 6))
        I = np.zeros(6)

        def stamp(a, b, r):
            if r == INF:
                return
            g = 1.0 / r
            Y[a, a] += g
            if b is not None:
                Y[b, b] += g
                Y[a, b] -= g
                Y[b, a] -= g

        if v_pol is None:
            r_src, e = p.r_int + p.r_pol, p.emf
        else:
            r_src, e = p.r_int, p.emf - v_pol
        stamp(_S, None, r_src)
        I[_S] += e / r_src
        stamp(_S, _B, p.r_main)
        stamp(_B, _M, p.r_conn_motor)

        # Motor: running motor in steady state is a resistor R_eq plus a
        # current source, from torque balance k*I = T + b*w and E_b = k*w.
        if stalled:
            r_m, x = p.motor_ra, 0.0
        else:
            r_m = p.motor_ra + p.motor_k ** 2 / p.motor_b
            x = p.motor_k * p.motor_torque / (p.motor_b * r_m)
        stamp(_M, _G, r_m)
        I[_M] -= x
        I[_G] += x

        if not p.lamp_open:
            stamp(_B, _L, p.r_conn_lamp)
        stamp(_L, _G, p.r_lamp)
        stamp(_B, _R, p.r_conn_load)
        stamp(_R, _G, p.r_load)
        stamp(_B, _G, p.r_parasitic)
        if test_on:
            stamp(_B, _G, p.r_test)
        stamp(_G, None, p.r_gnd)

        v = np.linalg.solve(Y, I)
        i_motor = (v[_M] - v[_G]) / r_m + x
        speed = 0.0 if stalled else (p.motor_k * i_motor - p.motor_torque) / p.motor_b
        if speed < 0:
            continue  # load torque exceeds what the motor can deliver: it stalls
        volts = {name: float(v[i]) for i, name in enumerate(NODES)}
        volts["neg"] = 0.0
        v_bg = v[_B] - v[_G]
        return Solution(
            v=volts,
            i_src=float((v[_S] - v[_B]) / p.r_main),
            i_motor=float(i_motor),
            i_lamp=float((v[_L] - v[_G]) / p.r_lamp),
            i_load=float((v[_R] - v[_G]) / p.r_load),
            i_parasitic=0.0 if p.r_parasitic == INF else float(v_bg / p.r_parasitic),
            i_test=float(v_bg / p.r_test) if test_on else 0.0,
            motor_speed=float(speed),
            stalled=stalled,
        )
    raise RuntimeError("circuit has no valid solution")


def pulse_response(p: CircuitParams, width_s: float):
    """Switch the test load on for width_s seconds.

    Returns (before, immediate, end) solutions. The immediate step shows the
    ohmic drop only; polarisation then relaxes towards its new steady value."""
    before = solve(p)
    v_pol0 = before.i_src * p.r_pol
    immediate = solve(p, test_on=True, v_pol=v_pol0)
    v_pol_inf = solve(p, test_on=True).i_src * p.r_pol
    v_pol_t = v_pol_inf + (v_pol0 - v_pol_inf) * math.exp(-width_s / p.tau_pol)
    end = solve(p, test_on=True, v_pol=v_pol_t)
    return before, immediate, end
