"""Bayesian active diagnosis (approach C).

Hypotheses are sets of up to two in-model fault atoms. Each hypothesis is
represented by K sampled benches (component tolerances, fault severities),
which keeps nuisance parameters consistent across one session's readings.
The next test is the one with the highest expected information gain per unit
cost. A misfit flag is raised when no hypothesis explains the readings.
"""
from __future__ import annotations

import math
from dataclasses import replace
from itertools import combinations

import numpy as np

from .faults import ATOM_ELEMENT, ATOMS, apply_atoms, sample_healthy
from .logsetup import get_logger
from .measure import DEFAULT_PULSE_MS, OBS_KEYS, TAPS, CURRENTS, kind, obs_key, selector, snapshot

log = get_logger("engine")

ACTIONS = ([("read_voltage", {"point": t}) for t in TAPS]
           + [("read_current", {"channel": c}) for c in CURRENTS]
           + [("load_test", {"mode": "pulse", "width_ms": DEFAULT_PULSE_MS}),
              ("load_test", {"mode": "steady"})])
_ACTION_INDEX = {(t, selector(t, a)): i for i, (t, a) in enumerate(ACTIONS)}


def hypotheses() -> list[frozenset]:
    hs = [frozenset()] + [frozenset([a]) for a in ATOMS]
    for a, b in combinations(ATOMS, 2):
        if a.startswith("F5") and b.startswith("F5"):
            continue  # two sensor faults at once: excluded from the model
        hs.append(frozenset([a, b]))
    return hs


def elements_of(h) -> list[str]:
    return sorted(ATOM_ELEMENT[a] for a in h)


def _entropy(p: np.ndarray, axis=-1) -> np.ndarray:
    p = np.clip(p, 1e-300, 1.0)
    return -(p * np.log2(p)).sum(axis=axis)


class BayesEngine:
    def __init__(self, n_samples: int = 48, seed: int = 0, sigma_v: float = 0.03, sigma_i: float = 0.02,
                 rel: float = 0.01, misfit_z: float = 4.5, stop_p: float = 0.95, min_eig_bits: float = 0.02,
                 n_eig_samples: int = 128):
        rng = np.random.default_rng(seed)
        self.hyps = hypotheses()
        self.H, self.K = len(self.hyps), n_samples
        self.sigma0 = {"v": sigma_v, "i": sigma_i}
        self.rel, self.misfit_z, self.stop_p = rel, misfit_z, stop_p
        self.min_eig_bits, self.n_eig = min_eig_bits, n_eig_samples

        prior = np.array([0.3 if len(h) == 0 else (0.5 / 7 if len(h) == 1 else 0.2 / (self.H - 8))
                          for h in self.hyps])
        self.logprior = np.log(prior / prior.sum())

        # pred[a]: (H, K, 2 lamp states, D);  p_open: (H, K)
        dims = [len(OBS_KEYS[obs_key(t, a)]) for t, a in ACTIONS]
        self.pred = [np.zeros((self.H, self.K, 2, d)) for d in dims]
        self.p_open = np.zeros((self.H, self.K))
        for hi, h in enumerate(self.hyps):
            for k in range(self.K):
                st = apply_atoms(sample_healthy(rng), h, rng)
                self.p_open[hi, k] = st.p_lamp_open
                for s, lamp_open in enumerate((False, True)):
                    if s == 1 and st.p_lamp_open == 0:
                        for P in self.pred:
                            P[hi, k, 1] = P[hi, k, 0]
                        continue
                    snap = snapshot(replace(st.params, lamp_open=lamp_open), st.sensor_offsets)
                    for ai, (t, a) in enumerate(ACTIONS):
                        self.pred[ai][hi, k, s] = snap[(t, selector(t, a))]
        self.kinds = [np.array([kind(key) == "v" for key in OBS_KEYS[obs_key(t, a)]]) for t, a in ACTIONS]
        self.reset()

    # ----- belief state -------------------------------------------------
    def reset(self, seed: int = 0):
        self.rng = np.random.default_rng(seed)
        self.LL = np.zeros((self.H, self.K))
        self.MAXZ = np.zeros((self.H, self.K))
        self.obs: list[tuple[int, np.ndarray]] = []
        self.failed: set[int] = set()

    def _sigma(self, ai: int, P: np.ndarray) -> np.ndarray:
        base = np.where(self.kinds[ai], self.sigma0["v"], self.sigma0["i"])
        return np.sqrt(base ** 2 + (self.rel * P) ** 2)

    def _loglik(self, ai: int, y: np.ndarray):
        """Log-likelihood and worst |z| of y for every (h, k). y: (..., D)."""
        P = self.pred[ai]
        sig = self._sigma(ai, P)
        y = y[..., None, None, None, :]
        z = (y - P) / sig
        logn = (-0.5 * z ** 2 - np.log(sig) - 0.5 * math.log(2 * math.pi)).sum(-1)  # (..., H, K, 2)
        po = self.p_open
        with np.errstate(divide="ignore"):
            mix = np.logaddexp(np.log1p(-po) + logn[..., 0], np.log(po) + logn[..., 1])
        zmax = np.abs(z).max(-1)
        zbest = np.where(po > 0, zmax.min(-1), zmax[..., 0])
        return mix, zbest

    def update(self, tool: str, args: dict, data: dict):
        ai = _ACTION_INDEX.get((tool, selector(tool, args)))
        if ai is None:
            return  # not an engine-modelled action (e.g. human_probe)
        y = np.array([data[k] for k in OBS_KEYS[obs_key(tool, args)]], dtype=float)
        mix, zbest = self._loglik(ai, y)
        self.LL += mix
        self.MAXZ = np.maximum(self.MAXZ, zbest)
        self.obs.append((ai, y))

    def mark_failed(self, tool: str, args: dict):
        ai = _ACTION_INDEX.get((tool, selector(tool, args)))
        if ai is not None:
            self.failed.add(ai)

    def _joint(self) -> np.ndarray:
        lw = self.logprior[:, None] + self.LL
        w = np.exp(lw - lw.max())
        return w / w.sum()

    def posterior(self) -> np.ndarray:
        return self._joint().sum(1)

    def misfit(self) -> bool:
        return bool(self.obs) and float(self.MAXZ.min()) > self.misfit_z

    def residuals(self, n: int = 3) -> list[dict]:
        """Largest residuals under the best-fitting sampled bench."""
        if not self.obs:
            return []
        hi, k = np.unravel_index(np.argmax(self.logprior[:, None] + self.LL), self.LL.shape)
        out = []
        for ai, y in self.obs:
            tool, args = ACTIONS[ai]
            P = self.pred[ai][hi, k]  # (2, D)
            s = 0 if self.p_open[hi, k] == 0 else int(np.argmin(np.abs(y - P).max(-1)))
            sig = self._sigma(ai, P[s])
            for key, yy, pp, ss in zip(OBS_KEYS[obs_key(tool, args)], y, P[s], sig):
                out.append({"tool": tool, "args": args, "reading": key, "measured": round(float(yy), 3),
                            "expected": round(float(pp), 3), "z": round(float((yy - pp) / ss), 1)})
        return sorted(out, key=lambda r: -abs(r["z"]))[:n]

    # ----- test selection -------------------------------------------------
    def eig(self, ai: int) -> float:
        W = self._joint()
        post = W.sum(1)
        P = self.pred[ai]
        idx = self.rng.choice(self.H * self.K, size=self.n_eig, p=W.ravel())
        hi, ki = np.divmod(idx, self.K)
        states = (self.rng.random(self.n_eig) < self.p_open[hi, ki]).astype(int)
        mean = P[hi, ki, states]
        y = mean + self.rng.normal(size=mean.shape) * self._sigma(ai, mean)
        mix, _ = self._loglik(ai, y)                          # (N, H, K)
        lw = np.log(np.clip(W, 1e-300, None))[None] + mix
        lw -= lw.max(axis=(1, 2), keepdims=True)
        w = np.exp(lw)
        post_new = w.sum(2) / w.sum(axis=(1, 2))[:, None]
        return float(_entropy(post) - _entropy(post_new).mean())

    def suggest(self, guard, n: int = 3) -> list[dict]:
        ranked = []
        for ai, (tool, args) in enumerate(ACTIONS):
            if ai in self.failed or guard.check(tool, args):
                continue
            gain = self.eig(ai)
            ranked.append({"tool": tool, "args": args, "eig_bits": round(gain, 3),
                           "cost": guard.cost_of(tool, args), "score": gain / guard.cost_of(tool, args)})
        return sorted(ranked, key=lambda r: -r["score"])[:n]

    def top(self, n: int = 5) -> list[dict]:
        post = self.posterior()
        order = np.argsort(-post)[:n]
        return [{"faults": elements_of(self.hyps[i]) or ["none"], "p": round(float(post[i]), 3)} for i in order]

    def diagnosis(self) -> dict:
        post = self.posterior()
        i = int(np.argmax(post))
        if self.misfit():
            status = "out_of_model"
        elif post[i] >= 0.7:
            status = "diagnosed"
        else:
            status = "inconclusive"
        return {"status": status, "elements": elements_of(self.hyps[i]) if status != "out_of_model" else [],
                "confidence": round(float(post[i]), 3), "top": self.top(3), "residuals": self.residuals()}

    # ----- approach C: engine alone ---------------------------------------
    def run(self, guard, seed: int = 0, max_steps: int = 30) -> dict:
        self.reset(seed)
        for _ in range(max_steps):
            if self.misfit() or self.posterior().max() >= self.stop_p:
                break
            options = self.suggest(guard, n=1)
            if not options or options[0]["eig_bits"] < self.min_eig_bits:
                break
            tool, args = options[0]["tool"], options[0]["args"]
            res = guard.call(tool, args)
            if res["status"] == "ok":
                self.update(tool, args, res["data"])
            else:
                self.mark_failed(tool, args)
        d = self.diagnosis()
        log.debug("engine diagnosis %s", d)
        return {"method": "bayes", **d}
