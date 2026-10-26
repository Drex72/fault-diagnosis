"""Calibration logic and the hardware scripts, run against mock devices."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from faultdiag import calib
from faultdiag.hwrun import hardware_scenarios
from faultdiag.mockdevice import MockBenchDevice, MockLink
from faultdiag.runmeta import LAGOS
from faultdiag.serialbench import SerialBench

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def script(name, *args, results: Path, timeout=300):
    env = {**os.environ, "FAULTDIAG_RESULTS_DIR": str(results)}
    return subprocess.run([PY, str(ROOT / "scripts" / name), *args], capture_output=True, text=True,
                          env=env, timeout=timeout)


def pts(pairs):
    return [{"raw": r, "meter": m, "raw_std": 0.004} for r, m in pairs]


def test_fit_channel():
    fit = calib.fit_channel(pts([(6.0, 6.0 * 1.02 - 0.05), (10.0, 10.0 * 1.02 - 0.05), (13.0, 13.0 * 1.02 - 0.05)]))
    assert fit["gain"] == pytest.approx(1.02, abs=1e-6) and fit["offset"] == pytest.approx(-0.05, abs=1e-6)
    with pytest.raises(ValueError):
        calib.fit_channel(pts([(6.0, 6.0)]))
    with pytest.raises(ValueError, match="range"):
        calib.fit_channel(pts([(1.0, 3.0), (2.0, 6.0)]))  # gain 3: a wiring fault, not a calibration


def record(cid, ts, source="hardware"):
    return {"id": cid, "timestamp": ts.isoformat(), "source": source,
            "channels": {"tap:src": {"points": pts([(6, 6), (13, 13)]), "gain": 1.01, "offset": 0.02,
                                     "residual_rms": 0.003}}}


def test_records_latest_and_freshness(tmp_path):
    now = datetime.now(LAGOS)
    assert "no calibration" in calib.check("x", tmp_path)
    calib.save(record("cal-20261001-0900", now - timedelta(days=40)), tmp_path)
    assert "days old" in calib.check("cal-20261001-0900", tmp_path)
    calib.save(record("cal-20261020-0900", now - timedelta(days=2)), tmp_path)
    calib.save(record("cal-20261025-0900", now, source="sim"), tmp_path)
    assert calib.latest(tmp_path)["id"] == "cal-20261020-0900"  # sim records never count
    assert "latest record" in calib.check("cal-20261001-0900", tmp_path)
    assert calib.check("cal-20261020-0900", tmp_path) is None
    with pytest.raises(FileExistsError):
        calib.save(record("cal-20261020-0900", now), tmp_path)


def test_firmware_accepts_calibration_payload():
    bench = SerialBench(MockLink(MockBenchDevice(seed=1)), hello_timeout_s=0.5)
    try:
        r = bench.request("calibration", calib.firmware_args(record("cal-20261020-0900", datetime.now(LAGOS))))
        assert r["status"] == "ok" and r["data"]["id"] == "cal-20261020-0900"
        assert r["data"]["taps"]["src"] == {"gain": 1.01, "offset": 0.02}
        bad = calib.firmware_args(record("cal-x", datetime.now(LAGOS)))
        bad["taps"]["src"]["gain"] = 5.0
        assert bench.request("calibration", bad)["status"] == "error"
    finally:
        bench.close()


def test_hardware_scenario_list():
    names = hardware_scenarios(None, final=False)
    assert "F1b" not in names and "H3" not in names and "TF1" in names
    assert "H3" in hardware_scenarios(None, final=True)
    with pytest.raises(ValueError):
        hardware_scenarios(["F1b"], final=False)
    with pytest.raises(ValueError, match="frozen"):
        hardware_scenarios(["H4"], final=False)


def test_fw_selftest_mock(tmp_path):
    r = script("fw_selftest.py", "--mock", results=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    rec = json.loads(next((tmp_path / "mock-serial" / "fw-selftest").glob("*.json")).read_text())
    assert rec["source"] == "sim" and rec["passed"]


def test_calibrate_then_refit_mock(tmp_path):
    r = script("calibrate.py", "--mock", "--samples", "4", results=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    rec = calib.latest(tmp_path / "mock-serial" / "calibration", "sim")
    assert rec["channels"]["tap:src"]["gain"] == pytest.approx(1 / 0.98, rel=0.01)
    r = script("refit_noise.py", "--cal-dir", str(tmp_path / "mock-serial" / "calibration"), "--source", "sim",
               results=tmp_path)
    assert r.returncode == 0 and "engine_sigma_v" in r.stdout, r.stdout + r.stderr


def test_run_hardware_mock_end_to_end(tmp_path):
    r = script("run_hardware.py", "--mock", "--methods", "bayes,tree", "--scenarios", "healthy,F2,TF1",
               "--trials", "1", "--settle", "0", results=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    out = Path(json.loads(r.stdout.strip().splitlines()[-1])["out"])
    sessions = [json.loads(x) for x in (out / "sessions.jsonl").read_text().splitlines()]
    truth = [json.loads(x) for x in (out / "truth.jsonl").read_text().splitlines()]
    assert len(sessions) == len(truth) == 6
    assert all("scenario" not in s and "truth" not in s and s["source"] == "sim" for s in sessions)
    assert "MOCK SERIAL RESULTS (simulation, not hardware)" in r.stdout and "Source: sim" in r.stdout
    summary = (out / "summary.md").read_text()
    r2 = script("run_hardware.py", "--grade", str(out), results=tmp_path)
    assert r2.returncode == 0 and (out / "summary.md").read_text() == summary


def test_run_hardware_refuses_frozen_and_missing_ports(tmp_path):
    r = script("run_hardware.py", "--mock", "--scenarios", "H3", results=tmp_path)
    assert r.returncode != 0 and "frozen" in r.stderr
    r = script("run_hardware.py", results=tmp_path)
    assert r.returncode != 0 and "--port" in r.stderr
