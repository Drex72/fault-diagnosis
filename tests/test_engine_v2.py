import sys
from pathlib import Path

import pytest
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from faultdiag.engine_v2 import BayesEngineV2  # noqa: E402
from faultdiag.engine_v2_coupled import BayesEngineV2Coupled, BayesEngineV2HumanCoupled  # noqa: E402
from faultdiag.engine_v2_balance import BayesEngineV2Balance  # noqa: E402
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


def test_topology_balance_trigger_is_structural():
    e = BayesEngineV2Balance(cross_branch_only=True, n_samples=8, n_eig_samples=8)
    source_motor = e.hyps.index(frozenset(["F1", "F3"]))
    same_motor_branch = e.hyps.index(frozenset(["F2", "F5@motor"]))
    cross_branch = e.hyps.index(frozenset(["F3", "F4"]))
    assert not e._should_balance_probe(source_motor)
    assert not e._should_balance_probe(same_motor_branch)
    assert e._should_balance_probe(cross_branch)


def test_human_coupled_topology_trigger_is_honored():
    e = BayesEngineV2HumanCoupled(
        cross_branch_only=True, n_samples=24, n_eig_samples=48
    )
    d = e.run(guard_for("C1", seed=3), seed=3)
    assert d["status"] == "diagnosed"
    assert d["elements"] == ["battery", "motor"]
    assert d["current_balance_probe"] is None
