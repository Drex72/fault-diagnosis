"""Coupled-particle Bayesian variants.

The original engine samples nuisance parameters independently for every
hypothesis. With a finite particle bank that can create artificial evidence:
two hypotheses whose predictions should be identical on all surviving sensors
may receive different likelihoods only because their 48 healthy-tolerance
samples happened to differ.

This module uses common random numbers:
- particle k has the same healthy component tolerances in every hypothesis;
- a fault atom has the same sampled severity in every hypothesis containing it.

That makes nested hypotheses differ only by the physical effect of the extra
fault, sharply reducing Monte-Carlo nuisance leakage.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np

from .engine import ACTIONS
from .engine_v2_balance import BayesEngineV2Balance
from .engine_v2_human import BayesEngineV2Human, HUMAN_PROBES
from .faults import ATOMS, FaultState, apply_atoms, sample_healthy
from .measure import OBS_KEYS, obs_key, selector, snapshot
from .circuit import solve


def _one_state(base, atoms, seed: int, k: int) -> FaultState:
    state = FaultState(base)
    for atom in sorted(atoms):
        ai = ATOMS.index(atom)
        # Stable, atom-specific stream: the same F2 severity is used for
        # particle k whether the hypothesis is {F2} or {F2,F5@motor}.
        rng = np.random.default_rng(np.random.SeedSequence([seed, 1, k, ai]))
        tmp = apply_atoms(state.params, (atom,), rng)
        state.params = tmp.params
        state.sensor_offsets.update(tmp.sensor_offsets)
        state.current_gain.update(tmp.current_gain)
        if tmp.p_lamp_open:
            state.p_lamp_open = tmp.p_lamp_open
    return state


def coupled_states(hyps, K: int, seed: int) -> list[list[FaultState]]:
    bases = [
        sample_healthy(np.random.default_rng(np.random.SeedSequence([seed, 0, k])))
        for k in range(K)
    ]
    return [[_one_state(bases[k], h, seed, k) for k in range(K)] for h in hyps]


def install_coupled_bank(engine, seed: int):
    states = coupled_states(engine.hyps, engine.K, seed)
    dims = [len(OBS_KEYS[obs_key(t, a)]) for t, a in ACTIONS]
    engine.pred = [np.zeros((engine.H, engine.K, 2, d)) for d in dims]
    engine.p_open = np.zeros((engine.H, engine.K))

    for hi in range(engine.H):
        for k in range(engine.K):
            st = states[hi][k]
            engine.p_open[hi, k] = st.p_lamp_open
            for si, lamp_open in enumerate((False, True)):
                # Keep both states available; _loglik already gives state 1
                # zero weight when p_open is zero.
                snap = snapshot(replace(st.params, lamp_open=lamp_open), st.sensor_offsets)
                for action_i, (tool, args) in enumerate(ACTIONS):
                    engine.pred[action_i][hi, k, si] = snap[(tool, selector(tool, args))]
    engine._coupled_states = states


class BayesEngineV2Coupled(BayesEngineV2Balance):
    def __init__(self, *args, seed: int = 0, **kwargs):
        self._model_seed = seed
        super().__init__(*args, seed=seed, **kwargs)
        install_coupled_bank(self, seed)
        self.reset()


class BayesEngineV2HumanCoupled(BayesEngineV2Human):
    def __init__(self, *args, seed: int = 0, **kwargs):
        self._model_seed = seed
        super().__init__(*args, seed=seed, **kwargs)
        install_coupled_bank(self, seed)
        self.human_pred = self._human_from_coupled_states()
        self.reset()

    def _human_from_coupled_states(self):
        out = {pair: np.zeros((self.H, self.K, 2)) for pair in HUMAN_PROBES}
        for hi in range(self.H):
            for k in range(self.K):
                st = self._coupled_states[hi][k]
                for si, lamp_open in enumerate((False, True)):
                    v = solve(replace(st.params, lamp_open=lamp_open)).v
                    for pair in HUMAN_PROBES:
                        out[pair][hi, k, si] = v[pair[0]] - v[pair[1]]
        return out
