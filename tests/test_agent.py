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
