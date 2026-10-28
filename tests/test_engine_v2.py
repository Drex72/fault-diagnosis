import sys
from pathlib import Path

import pytest
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from faultdiag.engine_v2 import BayesEngineV2  # noqa: E402
from faultdiag.engine_v2_coupled import BayesEngineV2Coupled  # noqa: E402
from faultdiag.engine import ACTIONS  # noqa: E402
from faultdiag.guard import PolicyGuard  # noqa: E402
from faultdiag.harness import make_bench  # noqa: E402


def guard_for(name, seed=3):
    return PolicyGuard(make_bench(name, seed))


@pytest.fixture(scope="module")
def engine_v2():
    # Smaller MC counts keep unit tests quick; experiment runs use defaults.
    return BayesEngineV2(n_samples=32, n_eig_samples=64)


@pytest.mark.parametrize(
    "name,truth",
    [
        ("healthy", []),
        ("F1", ["battery"]),
        ("F2", ["motor_connection"]),
        ("F3", ["motor"]),
        ("C1", ["battery", "motor"]),
    ],
)
def test_v2_keeps_core_in_model_diagnoses(engine_v2, name, truth):
    d = engine_v2.run(guard_for(name), seed=1)
    assert d["status"] == "diagnosed"
    assert d["elements"] == truth


def test_v2_suggestion_exposes_decision_value(engine_v2):
    engine_v2.reset(1)
    options = engine_v2.suggest(guard_for("healthy"), n=2)
    assert options
    assert {"risk_reduction", "eig_bits", "score", "cost"} <= set(options[0])


def test_v2_quarantines_impossible_scalar_current(engine_v2):
    engine_v2.reset(1)
    engine_v2.update("read_current", {"channel": "motor"}, {"amps": 100.0})
    assert engine_v2.suspect_current
    assert engine_v2.obs == []
    assert engine_v2.failed


def test_coupled_particles_do_not_create_evidence_for_invisible_sensor_fault():
    e = BayesEngineV2Coupled(n_samples=16, n_eig_samples=16)
    f2 = e.hyps.index(frozenset(["F2"]))
    f2_sensor = e.hyps.index(frozenset(["F2", "F5@motor"]))
    for ai, (tool, args) in enumerate(ACTIONS):
        if tool == "read_voltage" and args.get("point") == "motor":
            continue
        assert np.array_equal(e.pred[ai][f2], e.pred[ai][f2_sensor]), (tool, args)
