"""Hybrid agent (approach E) and LLM-only agent (approach D).

The LLM chooses each action. In hybrid mode it sees the Bayesian engine's
ranked hypotheses, suggested tests and misfit report; the engine keeps doing
the probability arithmetic. Every call is schema-validated; after repeated
invalid calls the hybrid falls back to the engine's top suggestion.

Works with any OpenAI-compatible local server: Ollama (http://localhost:11434/v1)
or llama.cpp's llama-server (http://localhost:8080/v1).
"""
from __future__ import annotations

import json
import re
import time
import urllib.request

from .logsetup import get_logger
from .tools import ELEMENTS, TOOL_SCHEMAS, openai_tools, validate_call

log = get_logger("agent")

SUBMIT_SCHEMA = {
    "description": "Finish the session. List every faulty element; use an empty list if the bench is healthy.",
    "parameters": {
        "type": "object",
        "properties": {
            "faults": {"type": "array", "maxItems": 3, "items": {
                "type": "object",
                "properties": {"element": {"type": "string", "enum": list(ELEMENTS)},
                               "description": {"type": "string"}},
                "required": ["element"], "additionalProperties": False}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence": {"type": "string"},
        },
        "required": ["faults", "confidence"], "additionalProperties": False,
    },
}
AGENT_TOOLS = {**{k: v for k, v in TOOL_SCHEMAS.items() if k != "get_history"}, "submit_diagnosis": SUBMIT_SCHEMA}

SYSTEM_PROMPT = """You are a diagnostic technician for a 12 V DC testbench.

Bench: a 12 V lead-acid battery feeds a bus through a fuse. Three branches hang off the bus,
each through its own connection: a DC motor, a 12 V lamp and a 24 ohm resistive load. All loads
return through one shared return node 'ret' to battery negative ('neg', 0 V). Voltage taps (src,
bus, motor, lamp, load) are read through a multiplexer, all relative to 'neg'. Current is only
sensed at the battery output (src) and on the motor branch. A 4 ohm test load can be pulsed
across the bus. The technician can measure any two points with a handheld meter, including 'ret'.

Physics you can rely on: a downstream tap cannot read above the bus. Battery current equals the
sum of branch currents (Kirchhoff). Lamp current is about V_lamp / 14.4 and load current about
V_load / 24 when the return path is healthy. A healthy battery drops about 0.03 ohm x test current
on a pulse.

Each turn you receive the remaining budget, the last result and a report from a Bayesian engine:
its most likely fault sets, its suggested next tests with expected information gain, a misfit flag
and the largest residuals. The engine only knows these faults: aged battery, bad motor connection,
motor stall, intermittent lamp connection, offset on the motor/lamp/load voltage sensor, and pairs
of these. It cannot imagine other faults. When the engine reports misfit, or its best explanation
contradicts the physics above, reason about elements outside its model and design a test.

Rules: call exactly one tool per turn. Prefer the engine's suggestion unless you have a reason.
Use human_probe only when the bench cannot measure what you need. If a reading fails or looks
implausible, work around it. Finish with submit_diagnosis."""

LLM_ONLY_PROMPT = SYSTEM_PROMPT.split("Each turn you receive")[0] + (
    "Each turn you receive the remaining budget and the last result. Call exactly one tool per turn. "
    "Finish with submit_diagnosis.")


class OpenAICompatLLM:
    def __init__(self, base_url: str, model: str, timeout_s: float = 180, temperature: float = 0.0):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.timeout_s, self.temperature = model, timeout_s, temperature

    def __call__(self, messages: list[dict], tools: list[dict]) -> dict:
        body = json.dumps({"model": self.model, "messages": messages, "tools": tools,
                           "temperature": self.temperature, "stream": False}).encode()
        req = urllib.request.Request(self.url, body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            return json.loads(resp.read())["choices"][0]["message"]


def parse_call(msg: dict):
    """Return (name, args, call_id, how) or raise ValueError."""
    calls = msg.get("tool_calls") or []
    if calls:
        fn = calls[0]["function"]
        args = fn.get("arguments") or "{}"
        return fn["name"], json.loads(args) if isinstance(args, str) else args, calls[0].get("id"), "tool_call"
    # Some small models put the call in plain text.
    m = re.search(r"\{.*\}", msg.get("content") or "", re.S)
    if m:
        obj = json.loads(m.group(0))
        name = obj.get("name") or obj.get("tool")
        args = obj.get("arguments") or obj.get("args") or obj.get("parameters") or {}
        if name:
            return name, args, None, "content"
    raise ValueError("no tool call found in reply")


class Agent:
    def __init__(self, llm, engine=None, max_steps: int = 20, max_retries: int = 2):
        self.llm, self.engine = llm, engine  # engine=None gives the LLM-only agent (D)
        self.max_steps, self.max_retries = max_steps, max_retries
        self.method = "hybrid" if engine is not None else "llm"

    def _state(self, guard, last=None) -> str:
        state = {"budget_left": guard.remaining, "last_result": last}
        if self.engine is not None:
            e = self.engine
            state["engine"] = {"top_hypotheses": e.top(5),
                               "suggested_tests": [{k: s[k] for k in ("tool", "args", "eig_bits", "cost")}
                                                   for s in e.suggest(guard, n=3)],
                               "model_misfit": e.misfit(), "largest_residuals": e.residuals(3)}
        return json.dumps(state)

    def _next_call(self, messages, stats):
        tools = openai_tools(AGENT_TOOLS)
        for attempt in range(self.max_retries + 1):
            t0 = time.perf_counter()
            msg = self.llm(messages, tools)
            stats["latency_s"].append(round(time.perf_counter() - t0, 3))
            stats["llm_calls"] += 1
            assistant = {"role": "assistant", "content": msg.get("content") or ""}
            if msg.get("tool_calls"):
                assistant["tool_calls"] = msg["tool_calls"][:1]
            messages.append(assistant)
            try:
                name, args, call_id, how = parse_call(msg)
                errors = validate_call(name, args, AGENT_TOOLS)
            except (ValueError, KeyError, json.JSONDecodeError) as exc:
                name, args, call_id, how, errors = None, None, None, None, [str(exc)]
                if msg.get("tool_calls"):
                    call_id = msg["tool_calls"][0].get("id")
            if not errors:
                stats["content_parsed"] += how == "content"
                return name, args, call_id
            stats["invalid"] += 1
            log.warning("invalid call (attempt %d): %s %s -> %s", attempt + 1, name, args, errors)
            feedback = f"Invalid tool call: {'; '.join(errors)}. Reply with exactly one valid tool call."
            messages.append({"role": "tool", "tool_call_id": call_id, "content": feedback} if call_id
                            else {"role": "user", "content": feedback})
        return None

    def run(self, guard, seed: int = 0) -> dict:
        if self.engine is not None:
            self.engine.reset(seed)
        stats = {"llm_calls": 0, "invalid": 0, "fallbacks": 0, "content_parsed": 0, "latency_s": []}
        prompt = SYSTEM_PROMPT if self.engine is not None else LLM_ONLY_PROMPT
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": self._state(guard)}]
        for _ in range(self.max_steps):
            got = self._next_call(messages, stats)
            if got is None:
                if self.engine is None:
                    break
                sugg = self.engine.suggest(guard, n=1)
                if not sugg:
                    break
                stats["fallbacks"] += 1
                name, args, call_id = sugg[0]["tool"], sugg[0]["args"], None
                log.warning("falling back to engine suggestion %s %s", name, args)
            else:
                name, args, call_id = got

            if name == "submit_diagnosis":
                elements = sorted({f["element"] for f in args["faults"]})
                return {"method": self.method, "status": "diagnosed", "elements": elements,
                        "confidence": args["confidence"], "evidence": args.get("evidence", ""), "agent_stats": stats}

            res = guard.call(name, args)
            if self.engine is not None:
                if res["status"] == "ok":
                    self.engine.update(name, res["args"], res["data"])
                elif res["status"] != "rejected":
                    self.engine.mark_failed(name, res["args"])
            last = {k: res.get(k) for k in ("tool", "args", "status", "data", "error", "reason")}
            if call_id:
                messages.append({"role": "tool", "tool_call_id": call_id, "content": json.dumps(last)})
            messages.append({"role": "user", "content": self._state(guard, last)})

        log.warning("agent ended without submitting a diagnosis")
        if self.engine is not None:
            d = self.engine.diagnosis()
            return {"method": self.method, **d, "status": "inconclusive" if d["status"] == "diagnosed" else d["status"],
                    "agent_stats": stats}
        return {"method": self.method, "status": "inconclusive", "elements": [], "agent_stats": stats}
