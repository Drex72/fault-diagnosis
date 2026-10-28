"""Robust experimental Bayesian diagnosis engine.

This is the integrated mathematical candidate built from mechanisms that
survived paired/fresh-seed evaluation:

- decision-risk active test selection (BayesEngineV2);
- scalar current-sensor sanity gating;
- a conservative aggregate model-adequacy check;
- one challenge of a confident compound catalogue explanation;
- an independent KCL residual audit/localiser for gross return-path or
  unaccounted-current mechanisms; and
- at most one Bayesian handheld-meter measurement when an automatic voltage
  channel fails, or when a sensor offset and a physical fault on that same
  branch are both plausible.

The original BayesEngine remains unchanged and is the comparison baseline.
"""
from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from .circuit import solve
from .engine import ACTIONS, _entropy
from .engine_v3 import BayesEngineV3Residual
from .faults import ATOM_ELEMENT, ATOMS
from .measure import PROBE_POINTS


class BayesEngineRobust(BayesEngineV3Residual):
    """Integrated mathematical engine candidate.

    Defaults are intentionally conservative. They come from development
    calibration followed by fresh-seed validation; they are still simulation
    settings until real-bench calibration exists.
    """

    def __init__(
        self,
        *args,
        sigma_probe: float = 0.015,
        max_human_probes: int = 1,
        sensor_ambiguity_p: float = 0.20,
        physical_ambiguity_p: float = 0.15,
        marginal_fault_p: float = 0.50,
        **kwargs,
    ):
        kwargs.setdefault("min_confirm_score", 1.0)
        kwargs.setdefault("challenge_complex_hypothesis", True)
        kwargs.setdefault("aggregate_misfit_z", 2.0)

        super().__init__(
            *args,
            return_ratio_threshold=10.0,
            hidden_sigma_threshold=15.0,
            audit_compound=True,
            **kwargs,
        )

        self.sigma_probe = sigma_probe
        self.max_human_probes = max_human_probes
        self.sensor_ambiguity_p = sensor_ambiguity_p
        self.physical_ambiguity_p = physical_ambiguity_p
        self.marginal_fault_p = marginal_fault_p

        self.probe_points = PROBE_POINTS
        self._probe_index = {point: i for i, point in enumerate(PROBE_POINTS)}
        self.clean_v = np.zeros((self.H, self.K, 2, len(PROBE_POINTS)))

        # V3 already reconstructed the exact state behind every (hypothesis,
        # particle), so the handheld-meter model can use physical node voltage
        # without the automated voltage sensor's injected offset.
        for hi, row in enumerate(self._states):
            for k, state in enumerate(row):
                for s, lamp_open in enumerate((False, True)):
                    sol = solve(replace(state.params, lamp_open=lamp_open))
                    for pi, point in enumerate(PROBE_POINTS):
                        self.clean_v[hi, k, s, pi] = sol.v[point]

    def reset(self, seed: int = 0):
        super().reset(seed)
        self.probe_obs: list[dict] = []

    # ----- handheld-meter likelihood -----------------------------------
    def _probe_means(self, point_a: str, point_b: str) -> np.ndarray:
        ia = self._probe_index[point_a]
        ib = self._probe_index[point_b]
        return self.clean_v[..., ia] - self.clean_v[..., ib]

    def _probe_sigma(self, mean: np.ndarray) -> np.ndarray:
        # Small relative term keeps the likelihood realistic at 12 V while the
        # fixed term covers the meter's low-level repeatability.
        return np.sqrt(self.sigma_probe**2 + (0.002 * np.abs(mean)) ** 2)

    def _probe_loglik(self, point_a: str, point_b: str, y: np.ndarray):
        P = self._probe_means(point_a, point_b)
        sig = self._probe_sigma(P)
        yy = np.asarray(y, dtype=float)[..., None, None, None]
        z = (yy - P) / sig
        logn = -0.5 * z**2 - np.log(sig) - 0.5 * math.log(2 * math.pi)
        po = self.p_open
        with np.errstate(divide="ignore"):
            mix = np.logaddexp(
                np.log1p(-po) + logn[..., 0],
                np.log(po) + logn[..., 1],
            )
        zbest = np.where(po > 0, np.abs(z).min(-1), np.abs(z[..., 0]))
        return mix, zbest

    def update(self, tool: str, args: dict, data: dict):
        if tool != "human_probe":
            return super().update(tool, args, data)

        y = np.array(float(data["volts"]))
        mix, zbest = self._probe_loglik(args["point_a"], args["point_b"], y)
        self.LL += mix
        self.MAXZ = np.maximum(self.MAXZ, zbest)
        self.probe_obs.append(
            {
                "point_a": args["point_a"],
                "point_b": args["point_b"],
                "volts": round(float(y), 4),
            }
        )

    def _future_probe_posteriors(
        self, point_a: str, point_b: str, W: np.ndarray
    ) -> np.ndarray:
        P = self._probe_means(point_a, point_b)
        flat = W.ravel()
        flat = flat / flat.sum()

        idx = self.rng.choice(self.H * self.K, size=self.n_eig, p=flat)
        hi, ki = np.divmod(idx, self.K)
        states = (
            self.rng.random(self.n_eig) < self.p_open[hi, ki]
        ).astype(int)
        mean = P[hi, ki, states]
        y = mean + self.rng.normal(size=mean.shape) * self._probe_sigma(mean)

        mix, _ = self._probe_loglik(point_a, point_b, y)
        lw = np.log(np.clip(W, 1e-300, None))[None] + mix
        lw -= lw.max(axis=(1, 2), keepdims=True)
        w = np.exp(lw)
        return w.sum(2) / w.sum(axis=(1, 2))[:, None]

    def _probe_metrics(self, point_a: str, point_b: str) -> tuple[float, float]:
        W = self._joint()
        post = W.sum(1)
        post_new = self._future_probe_posteriors(point_a, point_b, W)

        risk_now = 1.0 - float(post.max())
        risk_after = 1.0 - post_new.max(axis=1)
        risk_gain = max(0.0, risk_now - float(risk_after.mean()))
        eig = float(_entropy(post) - _entropy(post_new).mean())
        return risk_gain, eig

    # ----- observability / probe planning -------------------------------
    def atom_marginals(self) -> dict[str, float]:
        post = self.posterior()
        return {
            atom: float(
                sum(post[hi] for hi, hypothesis in enumerate(self.hyps) if atom in hypothesis)
            )
            for atom in ATOMS
        }

    def _failed_voltage_points(self) -> set[str]:
        points = set()
        for ai in self.failed:
            tool, args = ACTIONS[ai]
            if tool == "read_voltage":
                points.add(args["point"])
        return points

    def _ambiguous_sensor_points(self) -> set[str]:
        """Sensor/physical ambiguities worth paying for an independent meter.

        A sensor posterior alone is not enough: that made the experimental
        engine waste probes on easy pure sensor faults. Require a competing
        physical mechanism on the same branch too.
        """
        marg = self.atom_marginals()
        points = set()

        if (
            marg["F5@motor"] >= self.sensor_ambiguity_p
            and (marg["F2"] + marg["F3"]) >= self.physical_ambiguity_p
        ):
            points.add("motor")

        if (
            marg["F5@lamp"] >= self.sensor_ambiguity_p
            and marg["F4"] >= self.physical_ambiguity_p
        ):
            points.add("lamp")

        # There is no in-model physical load-branch connection fault to
        # distinguish from F5@load, so probing a pure load sensor hypothesis
        # adds cost without answering a competing physical explanation.
        return points

    def _probe_candidates(self, guard) -> list[dict]:
        if len(self.probe_obs) >= self.max_human_probes:
            return []

        points = self._failed_voltage_points() | self._ambiguous_sensor_points()
        candidates = []
        for point in sorted(points):
            pairs = [(point, "neg")]
            if point != "bus":
                pairs.append(("bus", point))

            for point_a, point_b in pairs:
                args = {"point_a": point_a, "point_b": point_b}
                if guard.check("human_probe", args):
                    continue

                risk_gain, eig = self._probe_metrics(point_a, point_b)
                cost = guard.cost_of("human_probe", args)
                candidates.append(
                    {
                        "tool": "human_probe",
                        "args": args,
                        "eig_bits": round(eig, 3),
                        "risk_reduction": round(risk_gain, 4),
                        "cost": cost,
                        "score": risk_gain / cost,
                    }
                )
        return candidates

    def suggest(self, guard, n: int = 3) -> list[dict]:
        # Keep enough automatic candidates to compare fairly with the expensive
        # probe; then return only the requested top n.
        ranked = super().suggest(guard, n=len(ACTIONS))
        ranked.extend(self._probe_candidates(guard))
        return sorted(
            ranked,
            key=lambda row: (-row["score"], -row["eig_bits"]),
        )[:n]

    # ----- terminal decision -------------------------------------------
    def diagnosis(self) -> dict:
        d = super().diagnosis()
        d["human_probes"] = list(self.probe_obs)
        marginals = self.atom_marginals()
        d["fault_marginals"] = {
            atom: round(probability, 3)
            for atom, probability in marginals.items()
        }

        failed_points = self._failed_voltage_points()
        if not failed_points or d["status"] == "out_of_model":
            return d

        # A voltage-offset fault on a channel that never produced a reading is
        # structurally unobservable. Do not let the joint MAP hypothesis assert
        # it merely because probability has nowhere else to go.
        selected_atoms = []
        masked_atoms = []
        for atom, probability in marginals.items():
            point = atom.split("@", 1)[1] if atom.startswith("F5@") else None
            if point in failed_points:
                masked_atoms.append(atom)
                continue
            if probability >= self.marginal_fault_p:
                selected_atoms.append(atom)

        selected = sorted({ATOM_ELEMENT[atom] for atom in selected_atoms})
        d["elements"] = selected
        d["unobservable_atoms"] = masked_atoms

        if selected:
            d["status"] = "diagnosed"
        elif masked_atoms:
            d["status"] = "inconclusive"
        return d

    def run(self, guard, seed: int = 0, max_steps: int = 30) -> dict:
        d = super().run(guard, seed=seed, max_steps=max_steps)

        # Even if a cost-sensitive planner did not choose the expensive DMM
        # during the main loop, one independent reading is mandatory before
        # finalising after a failed voltage channel. This is an observability
        # requirement, not a confidence override.
        if (
            self._failed_voltage_points()
            and not self.probe_obs
            and self.max_human_probes > 0
        ):
            candidates = self._probe_candidates(guard)
            if candidates:
                best = max(
                    candidates,
                    key=lambda row: (row["score"], row["eig_bits"]),
                )
                res = guard.call(best["tool"], best["args"])
                if res["status"] == "ok":
                    self.update(best["tool"], best["args"], res["data"])
                d = self.diagnosis()

        return {"method": "bayes_robust", **d}
