"""Agent behaviour with a misbehaving model server (no real model needed)."""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from faultdiag.agent import Agent, LLMUnavailable, OpenAICompatLLM  # noqa: E402
from faultdiag.engine import BayesEngine  # noqa: E402
from faultdiag.guard import PolicyGuard, load_policy  # noqa: E402
from faultdiag.harness import make_bench, run_trial  # noqa: E402


@pytest.fixture(scope="module")
def engine():
    return BayesEngine()


class _Handler(BaseHTTPRequestHandler):
    mode = "ok"

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        if self.mode == "slow":
            time.sleep(1.0)
        if self.mode == "404":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error": "model not found"}')
            return
        reply = {"choices": [{"message": {"role": "assistant", "content": "I think the motor is fine."}}]}
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(reply).encode())


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


def _llm(srv, mode, timeout=5.0):
    _Handler.mode = mode
    return OpenAICompatLLM(f"http://127.0.0.1:{srv.server_port}/v1", "m", timeout_s=timeout)


def test_http_error_is_llm_unavailable(server):
    with pytest.raises(LLMUnavailable, match="HTTP 404"):
        _llm(server, "404")([{"role": "user", "content": "x"}], [])


def test_unreachable_server_is_llm_unavailable():
    with pytest.raises(LLMUnavailable, match="cannot reach"):
        OpenAICompatLLM("http://127.0.0.1:9/v1", "m", timeout_s=1)([{"role": "user", "content": "x"}], [])


def test_slow_reply_is_timeout(server):
    with pytest.raises(TimeoutError):
        _llm(server, "slow", timeout=0.2)([{"role": "user", "content": "x"}], [])


def test_timeout_counts_invalid_and_falls_back(server, engine):
    g = PolicyGuard(make_bench("F2", 1))
    d = Agent(_llm(server, "slow", timeout=0.2), engine, max_steps=2).run(g, seed=1)
    s = d["agent_stats"]
    assert s["timeouts"] == 2 and s["invalid"] == 2 and s["fallbacks"] == 2 and len(g.history) == 2


def test_plain_text_reply_without_call_is_counted(server, engine):
    d = Agent(_llm(server, "ok"), engine, max_steps=1, max_retries=1).run(PolicyGuard(make_bench("F2", 1)), seed=1)
    s = d["agent_stats"]
    assert s["no_call"] == 2 and s["invalid"] == 2 and s["fallbacks"] == 1


def test_unreachable_model_stops_the_run(engine):
    llm = OpenAICompatLLM("http://127.0.0.1:9/v1", "m", timeout_s=1)
    with pytest.raises(ConnectionError):
        run_trial("F2", "hybrid", Agent(llm, engine).run, 1, load_policy())


# ---- Phase 2: loop detection, context trimming, evidence check ---------------
from faultdiag.evidence import check_evidence  # noqa: E402


class Scripted:
    def __init__(self, replies):
        self.replies, self.sent = list(replies), []

    def __call__(self, messages, tools):
        self.sent.append((messages, [t["function"]["name"] for t in tools]))
        return self.replies.pop(0)


def tc(name, args, i=0, content=""):
    return {"content": content, "tool_calls": [{"id": f"c{i}", "type": "function",
                                                "function": {"name": name, "arguments": json.dumps(args)}}]}


def test_loop_detection_ends_session_with_submit_only_turn(engine):
    llm = Scripted([tc("read_voltage", {"point": "bus"}, i) for i in range(3)]
                   + [tc("submit_diagnosis", {"faults": [{"element": "motor_connection"}], "confidence": 0.6}, 9)])
    g = PolicyGuard(make_bench("F2", 1))
    d = Agent(llm, engine).run(g, seed=1)
    assert d["agent_stats"]["loop_detected"] and len(g.history) == 3
    assert llm.sent[-1][1] == ["submit_diagnosis"]  # the final turn only offers submit
    assert d["status"] == "diagnosed" and d["elements"] == ["motor_connection"]


def test_repeated_rejected_call_is_a_loop(engine):
    bad = {"point_a": "ret", "point_b": "ret"}  # rejected by the guard every time
    llm = Scripted([tc("human_probe", bad, i) for i in range(3)] + [{"content": "no idea"}] * 3)
    g = PolicyGuard(make_bench("F2", 1))
    d = Agent(llm, engine).run(g, seed=1)
    assert d["agent_stats"]["loop_detected"] and g.history == [] and d["status"] != "diagnosed"


def test_repeat_with_changing_reading_is_not_a_loop(engine):
    class Flipping:  # lamp tap alternates, as with an intermittent connection
        def __init__(self):
            self.n = 0

        def execute(self, tool, args):
            self.n += 1
            return {"status": "ok", "data": {"volts": 12.5 if self.n % 2 else 0.4}}

    llm = Scripted([tc("read_voltage", {"point": "lamp"}, i) for i in range(4)]
                   + [tc("submit_diagnosis", {"faults": [{"element": "lamp_connection"}], "confidence": 0.7}, 9)])
    d = Agent(llm, None).run(PolicyGuard(Flipping()), seed=1)
    assert not d["agent_stats"]["loop_detected"] and d["elements"] == ["lamp_connection"]


def test_context_is_trimmed_for_long_sessions(engine):
    points = ["src", "bus", "motor", "lamp", "load"] * 2
    llm = Scripted([tc("read_voltage", {"point": p}, i) for i, p in enumerate(points)]
                   + [tc("submit_diagnosis", {"faults": [], "confidence": 0.5}, 99)])
    g = PolicyGuard(make_bench("healthy", 1))
    d = Agent(llm, engine, max_context_chars=6000).run(g, seed=1)
    s = d["agent_stats"]
    assert s["trimmed_turns"] > 0 and s["max_context_chars"] < 9000
    last, _ = llm.sent[-1]
    assert last[0]["role"] == "system" and "Earlier steps" in last[1]["content"]
    assert all(f"{i}. read_voltage" in last[1]["content"] for i in range(1, 11))  # every call kept
    assert '"engine"' in last[1]["content"]  # latest engine report kept
    assert not any(m["role"] == "tool" for m in last)  # no orphaned tool replies


def test_evidence_check_flags_readings_never_taken():
    history = [{"tool": "read_voltage", "args": {"point": "bus"}, "status": "ok", "data": {"volts": 12.6123}},
               {"tool": "read_voltage", "args": {"point": "motor"}, "status": "ok", "data": {"volts": 11.8411}},
               {"tool": "read_current", "args": {"channel": "motor"}, "status": "ok", "data": {"amps": 0.4012}}]
    c = check_evidence("bus 12.61 V, motor 11.84V, drop 0.77 V, 401 mA, connection about 1.9 ohm", history)
    assert c["verified"] == ["12.61 V", "11.84V", "401 mA"] and set(c["derived"]) == {"0.77 V", "1.9 ohm"}
    assert not c["flag"]
    c = check_evidence("lamp tap 3.2 V and the human probe showed 0.4 V", history)
    assert c["unverified"] == ["3.2 V", "0.4 V"] and c["missing_tools"] == ["human_probe"] and c["flag"]


def test_submission_record_carries_evidence_check(engine):
    llm = Scripted([tc("read_voltage", {"point": "bus"}, 0),
                    tc("submit_diagnosis", {"faults": [], "confidence": 0.5, "evidence": "motor tap 3.1 V"}, 1)])
    d = Agent(llm, engine).run(PolicyGuard(make_bench("healthy", 1)), seed=1)
    assert d["evidence_check"]["unverified"] == ["3.1 V"] and d["evidence_check"]["flag"]
    assert d["turns"][0]["tool"] == "read_voltage" and d["turns"][-1]["tool"] == "submit_diagnosis"
