"""The dashboard's API, run against a real server on a free port with temporary data."""
import http.client
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

import pytest

from gui.live import GuiProber, Hub
from gui.server import make_server
from gui.store import Store

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def cli(name, *args, results: Path):
    env = {**os.environ, "FAULTDIAG_RESULTS_DIR": str(results)}
    r = subprocess.run([PY, str(ROOT / "scripts" / name), *args], capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    return r


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    """One simulated experiment and one mock-serial (hardware-style, blind) run, made by the CLI scripts."""
    res = tmp_path_factory.mktemp("results")
    exp = cli("run_experiment.py", "--methods", "fixed,bayes", "--scenarios", "healthy,F2,H1", "--trials", "2", results=res)
    hw = cli("run_hardware.py", "--mock", "--methods", "bayes", "--scenarios", "healthy,F2", "--trials", "1",
             "--settle", "0", results=res)
    exp_dir = next(p.parent for p in res.glob("*/sessions.jsonl"))
    hw_dir = Path(json.loads(hw.stdout.strip().splitlines()[-1])["out"])
    return {"root": res, "exp": exp_dir, "exp_stdout": exp.stdout, "hw": hw_dir}


@pytest.fixture()
def server(results, tmp_path):
    srv, app = make_server("127.0.0.1", 0, results["root"], tmp_path / "gui.sqlite", cal_dir=tmp_path / "cal")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1], app
    app.live.shutdown()
    srv.shutdown()
    srv.server_close()


def call(port, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    c.request(method, path, body=json.dumps(body) if body is not None else None,
              headers={"Content-Type": "application/json"} if body is not None else {})
    r = c.getresponse()
    raw = r.read()
    c.close()
    ctype = r.getheader("Content-Type", "")
    return r.status, (json.loads(raw) if ctype.startswith("application/json") else raw.decode())


def wait_idle(port, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if call(port, "GET", "/api/status")[1]["session"] is None:
            return
        time.sleep(0.2)
    raise AssertionError("session did not finish")


def run_id(results, key):
    return str(results[key].relative_to(results["root"]))


# ---------------------------------------------------------------- static files and status
def test_pages_and_static_files(server):
    port, _ = server
    code, body = call(port, "GET", "/")
    assert code == 200 and "Bench diagnosis" in body and "/static/app.js" in body
    for f in ("app.js", "style.css"):
        assert call(port, "GET", f"/static/{f}")[0] == 200
    assert call(port, "GET", "/static/../server.py")[0] == 404  # no path traversal
    assert call(port, "GET", "/static/nope.js")[0] == 404
    assert call(port, "GET", "/api/nope")[0] == 404
    for asset in ("app.js", "style.css", "index.html"):  # nothing is fetched from the internet
        text = (ROOT / "gui" / "static" / asset).read_text()
        assert "http://" not in text.replace("http://www.w3.org/2000/svg", "") and "https://" not in text


def test_status_and_options(server):
    port, _ = server
    code, st = call(port, "GET", "/api/status")
    assert code == 200 and st["session"] is None and st["bench"] == {"connected": False, "mode": None}
    code, o = call(port, "GET", "/api/options")
    assert code == 200 and o["methods"] == ["fixed", "tree", "bayes", "llm", "hybrid"]
    assert "H3" not in o["sim_scenarios"] and "H4" not in o["bench_scenarios"]  # frozen until the final run


# ---------------------------------------------------------------- experiments
def test_experiment_summary_is_the_cli_summary(server, results):
    port, _ = server
    code, runs = call(port, "GET", "/api/runs")
    ids = {r["id"]: r for r in runs}
    exp_id, hw_id = run_id(results, "exp"), run_id(results, "hw")
    assert ids[exp_id]["source"] == "sim" and ids[exp_id]["kind"] == "experiment"
    assert ids[hw_id]["kind"] == "mock serial" and ids[hw_id]["source"] == "sim"
    code, s = call(port, "GET", f"/api/runs/summary?run={quote(exp_id)}")
    assert code == 200
    cli_md = (results["exp"] / "summary.md").read_text()
    assert s["markdown"] == cli_md  # byte for byte what the CLI saved
    assert cli_md.strip() in results["exp_stdout"]  # and what it printed
    table_rows = [line for line in cli_md.splitlines() if line.startswith("| ")][1:]  # minus the header
    assert len(s["rows"]) == len(table_rows)
    assert [c["scenario"] for c in s["scenarios"]] == ["healthy", "F2", "H1"]
    cell = s["scenarios"][1]["cells"]["bayes"]
    assert cell["n"] == 2 and 0 <= cell["correct"] <= 2
    assert {p["method"] for p in s["cost_accuracy"]} == {"fixed", "bayes"}


def test_hardware_style_summary_is_the_graded_cli_summary(server, results):
    port, _ = server
    code, s = call(port, "GET", f"/api/runs/summary?run={quote(run_id(results, 'hw'))}")
    assert code == 200 and s["markdown"] == (results["hw"] / "summary.md").read_text()
    assert "Interrupted sessions" in s["markdown"]


def test_session_drilldown_and_csv(server, results):
    port, _ = server
    rid = quote(run_id(results, "exp"))
    code, idx = call(port, "GET", f"/api/runs/sessions?run={rid}")
    assert code == 200 and len(idx) == 12  # 3 scenarios x 2 trials x 2 methods
    code, one = call(port, "GET", f"/api/runs/session?run={rid}&index=3")
    assert code == 200 and one["history"] and one["source"] == "sim"
    assert call(port, "GET", f"/api/runs/session?run={rid}&index=99")[0] == 404
    assert call(port, "GET", "/api/runs/summary?run=..%2F..%2Fetc")[0] == 404
    assert call(port, "GET", "/api/runs/summary?run=nope")[0] == 404
    code, csv_text = call(port, "GET", f"/api/runs/csv?run={rid}")
    lines = csv_text.strip().splitlines()
    assert code == 200 and lines[0].startswith("run,source,") and len(lines) == 13


def test_models_and_calibration_pages(server):
    port, _ = server
    code, m = call(port, "GET", "/api/models")
    assert code == 200 and m["source"] == "sim" and m["latest"] == []
    code, c = call(port, "GET", "/api/calibration")
    assert code == 200 and c["latest"] is None and "No hardware calibration" in c["warning"]


# ---------------------------------------------------------------- tracker
def test_tracker_crud_and_export(server):
    port, _ = server
    code, plan = call(port, "GET", "/api/tracker/milestones")
    assert code == 200 and len(plan) == 14 and all(r["status"] == "todo" for r in plan)
    code, row = call(port, "POST", "/api/tracker/bom", {"item": "Spare INA226", "qty": "1"})
    assert code == 200 and row["status"] == "needed"
    code, row = call(port, "PUT", f"/api/tracker/bom/{row['id']}", {"status": "ordered", "price_ngn": "4500", "supplier": "Lagos market"})
    assert code == 200 and row["status"] == "ordered" and row["price_ngn"] == "4500"
    assert call(port, "PUT", f"/api/tracker/bom/{row['id']}", {"status": "lost"})[0] == 400
    assert call(port, "PUT", f"/api/tracker/bom/{row['id']}", {"colour": "red"})[0] == 400
    assert call(port, "PUT", "/api/tracker/bom/99999", {"status": "ordered"})[0] == 404
    assert call(port, "GET", "/api/tracker/nope")[0] == 404
    code, md = call(port, "GET", "/api/tracker/export.md")
    assert code == 200 and "## Bill of materials" in md and "Spare INA226" in md and "| ordered |" in md
    assert call(port, "DELETE", f"/api/tracker/bom/{row['id']}")[0] == 200
    assert "Spare INA226" not in call(port, "GET", "/api/tracker/export.md")[1]


# ---------------------------------------------------------------- live sessions
def test_sim_session_streams_steps_and_saves_a_sim_record(server, results):
    port, app = server
    assert call(port, "POST", "/api/session/start", {"mode": "sim", "method": "bayes", "scenario": "H3"})[0] == 400
    assert call(port, "POST", "/api/session/start", {"mode": "sim", "method": "magic"})[0] == 400
    assert call(port, "POST", "/api/session/start", {"mode": "mock", "method": "bayes"})[0] == 400  # not connected
    code, r = call(port, "POST", "/api/session/start", {"mode": "sim", "method": "bayes", "scenario": "F2", "seed": 7})
    assert code == 200
    wait_idle(port)
    code, evs = call(port, "GET", f"/api/sessions/{r['id']}/events")
    kinds = [e["kind"] for e in evs]
    assert kinds[0] == "session_start" and kinds[-1] == "session_end"
    assert "step" in kinds and "engine" in kinds
    eng = next(e["payload"] for e in evs if e["kind"] == "engine")
    assert {"top", "misfit", "residuals"} <= set(eng) and eng["top"][0]["p"] > 0
    end = evs[-1]["payload"]
    assert end["scenario"] == "F2" and end["status"] in ("diagnosed", "inconclusive", "out_of_model")
    saved = [json.loads(x) for f in (results["root"] / "live").glob("sim-*/sessions.jsonl") for x in f.read_text().splitlines()]
    mine = [x for x in saved if x["session_id"] == r["id"]]
    assert len(mine) == 1 and mine[0]["source"] == "sim" and mine[0]["via"] == "gui-sim" and mine[0]["policy_hash"]
    assert call(port, "GET", "/api/sessions/nope/events")[0] == 404
    assert call(port, "GET", "/api/sessions")[1][0]["id"] == r["id"]


def read_sse(port, path, headers, want):
    """Reads the event stream until `want` events arrived; returns (raw text, events)."""
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers=headers)
    r = c.getresponse()
    assert r.status == 200 and r.getheader("Content-Type") == "text/event-stream"
    raw, events = "", []
    while len(events) < want:
        line = r.fp.readline().decode()
        raw += line
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    c.close()
    return raw, events


def test_event_stream_replays_after_last_event_id(server):
    port, app = server
    for i in range(3):
        app.hub.publish(None, "log", {"level": "INFO", "logger": "test", "msg": f"m{i}"})
    first = app.store.last_event_id() - 2
    raw, evs = read_sse(port, "/api/events", {"Last-Event-ID": str(first)}, 2)
    assert raw.startswith("retry: 2000") and f"id: {first + 1}" in raw
    assert [e["payload"]["msg"] for e in evs] == ["m1", "m2"]
    # a second tab sees the same live events
    got = []
    t = threading.Thread(target=lambda: got.extend(read_sse(port, f"/api/events?after={first + 2}", {}, 1)[1]))
    t.start()
    time.sleep(0.5)
    app.hub.publish(None, "log", {"level": "INFO", "logger": "test", "msg": "live"})
    t.join(10)
    assert got and got[0]["payload"]["msg"] == "live"


def test_mock_bench_session_probe_and_safe_state(server):
    port, app = server
    assert call(port, "POST", "/api/bench/connect", {"mode": "hardware"})[0] == 400  # ports are required
    code, b = call(port, "POST", "/api/bench/connect", {"mode": "mock"})
    assert code == 200 and b["connected"] and b["firmware"] == "bench-mock-0.1.0" and b["injector_fw"]
    assert call(port, "POST", "/api/probe", {"volts": 1.0})[0] == 409  # nobody is waiting
    assert call(port, "POST", "/api/probe", {"volts": "abc"})[0] == 400
    code, r = call(port, "POST", "/api/session/start", {"mode": "mock", "method": "bayes", "scenario": "F2", "seed": 3})
    assert code == 200
    wait_idle(port)
    evs = call(port, "GET", f"/api/sessions/{r['id']}/events")[1]
    end = evs[-1]["payload"]
    assert evs[-1]["kind"] == "session_end" and end["scenario"] == "F2" and end["status"] != "crashed", end
    code, safe = call(port, "POST", "/api/safe_state")
    assert code == 200 and safe["sent"] and safe["response"]["status"] == "ok"
    assert call(port, "GET", "/api/status")[1]["bench"]["inhibited"] is True
    code, err = call(port, "POST", "/api/session/start", {"mode": "mock", "method": "bayes", "scenario": "F2"})
    assert code == 409 and "safe state is latched" in err["error"]  # never cleared automatically
    code, res = call(port, "POST", "/api/resume")
    assert code == 200 and res["status"] == "ok" and call(port, "GET", "/api/status")[1]["bench"]["inhibited"] is False
    assert call(port, "POST", "/api/bench/disconnect")[0] == 200
    assert call(port, "GET", "/api/status")[1]["bench"]["connected"] is False
    assert call(port, "POST", "/api/safe_state")[1]["sent"] is False


def test_gui_prober_waits_for_the_typed_reading(tmp_path):
    hub = Hub(Store(tmp_path / "p.sqlite"))
    prober = GuiProber(hub, hint=lambda a, b: 12.3)
    prober.session_id = "s1"
    out = []
    t = threading.Thread(target=lambda: out.append(prober("src", "ret", None, 5)))
    t.start()
    for _ in range(50):
        if prober.waiting:
            break
        time.sleep(0.05)
    assert prober.submit(12.25)
    t.join(5)
    assert out == [12.25] and not prober.submit(1.0)
    kinds = [e["kind"] for e in hub.store.events(0, "s1")]
    assert kinds == ["probe_request", "probe_done"]
    req = hub.store.events(0, "s1")[0]["payload"]
    assert req["hint"] == 12.3 and "src" in req["instruction"]


def test_server_restart_marks_running_session_interrupted(results, tmp_path):
    db = tmp_path / "gui.sqlite"
    Store(db).new_session("20261026-000000-abcd", mode="sim", source="sim", method="bayes", model=None, scenario="F2")
    srv, app = make_server("127.0.0.1", 0, results["root"], db, cal_dir=tmp_path / "cal")
    try:
        assert app.interrupted_on_start == ["20261026-000000-abcd"]
        assert app.store.session("20261026-000000-abcd")["status"] == "interrupted"
        last = app.store.events(0, "20261026-000000-abcd")[-1]
        assert last["kind"] == "session_end" and "restarted" in last["payload"]["reason"]
    finally:
        srv.server_close()
