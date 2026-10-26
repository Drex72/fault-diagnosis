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
import urllib.error
import urllib.request

from .evidence import check_evidence
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


class LLMUnavailable(ConnectionError):
    """The model server cannot be reached. Fatal for the run, unlike a timeout."""


class LLMBadOutput(ValueError):
    """The server is up but could not parse the model's own reply (e.g. Ollama's HTTP 500
    "XML syntax error" on a malformed tool call). A bad answer, not a dead server."""


# Ollama and llama-server error texts for a reply they could not parse into a tool call.
_PARSE_ERROR = re.compile(r"syntax error|failed to parse|parsing|unmarshal|invalid character", re.I)


class OpenAICompatLLM:
    def __init__(self, base_url: str, model: str, timeout_s: float = 180, temperature: float = 0.0,
                 extra: dict | None = None):
        """extra: additional request fields, e.g. {"reasoning_effort": "none"} to
        switch off a thinking model's reasoning on Ollama."""
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.timeout_s, self.temperature = model, timeout_s, temperature
        self.extra = extra or {}

    def __call__(self, messages: list[dict], tools: list[dict]) -> dict:
        body = json.dumps({"model": self.model, "messages": messages, "tools": tools,
                           "temperature": self.temperature, "stream": False, **self.extra}).encode()
        req = urllib.request.Request(self.url, body, {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return json.loads(resp.read())["choices"][0]["message"]
        except urllib.error.HTTPError as exc:  # server up, request refused (e.g. model not pulled)
            detail = exc.read()[:300].decode("utf-8", "replace")
            if exc.code >= 500 and _PARSE_ERROR.search(detail):
                raise LLMBadOutput(f"{self.url} could not parse the model's reply (HTTP {exc.code}): {detail}") from exc
            raise LLMUnavailable(f"{self.url} returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise TimeoutError(f"no reply from {self.model} within {self.timeout_s} s") from exc
            raise LLMUnavailable(f"cannot reach model server at {self.url}: {exc.reason}") from exc


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


LOOP_REPEATS = 3            # the same call this many times in a row with no new information ends the session
MAX_CONTEXT_CHARS = 36000   # about 12k tokens: under a 16k-token llama.cpp slot and Ollama's 32k window
KEEP_TURNS = 4              # when trimming, the latest turns are still sent word for word


def _no_new_info(a: dict, b: dict) -> bool:
    """True if result b tells nothing a did not: same status, readings within noise."""
    if a["status"] != b["status"]:
        return False
    da, db = a.get("data") or {}, b.get("data") or {}
    if set(da) != set(db):
        return False
    return all(abs(da[k] - db[k]) <= max(0.1, 0.02 * abs(da[k])) for k in da if isinstance(da[k], (int, float)))


def _line(n: int, c: dict) -> str:
    out = c.get("data") if c["status"] == "ok" else (c.get("error") or c.get("reason"))
    return f"{n}. {c['tool']} {json.dumps(c['args'], sort_keys=True)} -> {c['status']}: {json.dumps(out)}"


class Agent:
    def __init__(self, llm, engine=None, max_steps: int = 20, max_retries: int = 2,
                 max_context_chars: int = MAX_CONTEXT_CHARS):
        self.llm, self.engine = llm, engine  # engine=None gives the LLM-only agent (D)
        self.max_steps, self.max_retries = max_steps, max_retries
        self.max_context_chars = max_context_chars
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

    def _context(self, messages: list[dict], state_idxs: list[int], calls: list[dict], stats: dict) -> list[dict]:
        """What is sent to the model. Sessions that fit get the full transcript; longer
        ones get the system prompt, a compact history of the older calls with the state
        after them, then the latest KEEP_TURNS turns word for word (the model's own
        calls, their results and the latest engine report), so it keeps its thread."""
        if len(json.dumps(messages)) <= self.max_context_chars or len(calls) <= KEEP_TURNS:
            return messages
        stats["trimmed_turns"] += 1
        n_old = len(calls) - KEEP_TURNS
        cut = state_idxs[n_old]  # the state message sent after call n_old
        compact = ("Earlier steps, compacted to save context:\n"
                   + "\n".join(_line(i + 1, c) for i, c in enumerate(calls[:n_old]))
                   + f"\n\nState after step {n_old}:\n" + messages[cut]["content"])
        return [messages[0], {"role": "user", "content": compact}] + messages[cut + 1:]

    def _next_call(self, messages, stats, context=lambda m: m, schemas=AGENT_TOOLS, turns=None):
        tools = openai_tools(schemas)
        for attempt in range(self.max_retries + 1):
            ctx = context(messages)
            stats["max_context_chars"] = max(stats["max_context_chars"], len(json.dumps(ctx)))
            t0 = time.perf_counter()
            log.debug("context sent: %d messages, %d chars", len(ctx), len(json.dumps(ctx)))
            try:
                msg = self.llm(ctx, tools)
            except TimeoutError as exc:  # count as invalid and fall back at once: a retry would wait again
                stats["latency_s"].append(round(time.perf_counter() - t0, 3))
                stats["llm_calls"] += 1
                stats["invalid"] += 1
                stats["timeouts"] += 1
                log.warning("model timed out (attempt %d): %s", attempt + 1, exc)
                return None
            except LLMBadOutput as exc:  # same reply again at temperature 0, so fall back at once
                stats["latency_s"].append(round(time.perf_counter() - t0, 3))
                stats["llm_calls"] += 1
                stats["invalid"] += 1
                stats["server_parse_errors"] += 1
                log.warning("server rejected the model's reply (attempt %d): %s", attempt + 1, exc)
                return None
            stats["latency_s"].append(round(time.perf_counter() - t0, 3))
            stats["llm_calls"] += 1
            assistant = {"role": "assistant", "content": msg.get("content") or ""}
            if msg.get("tool_calls"):
                assistant["tool_calls"] = msg["tool_calls"][:1]
            messages.append(assistant)
            try:
                name, args, call_id, how = parse_call(msg)
                errors = validate_call(name, args, schemas)
            except (ValueError, KeyError, json.JSONDecodeError) as exc:
                name, args, call_id, how, errors = None, None, None, None, [str(exc)]
                if msg.get("tool_calls"):
                    call_id = msg["tool_calls"][0].get("id")
            if turns is not None:
                turns.append({"content": (msg.get("content") or "")[:600], "tool": name, "args": args,
                              "how": how, "valid": not errors})
            if not errors:
                stats["content_parsed"] += how == "content"
                return name, args, call_id
            stats["invalid"] += 1
            stats["no_call"] += how is None and not msg.get("tool_calls")
            log.warning("invalid call (attempt %d): %s %s -> %s", attempt + 1, name, args, errors)
            feedback = f"Invalid tool call: {'; '.join(errors)}. Reply with exactly one valid tool call."
            messages.append({"role": "tool", "tool_call_id": call_id, "content": feedback} if call_id
                            else {"role": "user", "content": feedback})
        return None

    def _submitted(self, args: dict, guard, stats: dict, turns: list) -> dict:
        check = check_evidence(args.get("evidence", ""), guard.history)
        if check["flag"]:
            log.warning("evidence cites readings not in the history: %s %s", check["unverified"],
                        check["missing_tools"])
        log.info("submitted %s, evidence: %.200s", [f["element"] for f in args["faults"]], args.get("evidence", ""))
        return {"method": self.method, "status": "diagnosed",
                "elements": sorted({f["element"] for f in args["faults"]}),
                "confidence": args["confidence"], "evidence": args.get("evidence", ""), "evidence_check": check,
                "turns": turns, "agent_stats": stats}

    def run(self, guard, seed: int = 0) -> dict:
        if self.engine is not None:
            self.engine.reset(seed)
        stats = {"llm_calls": 0, "invalid": 0, "fallbacks": 0, "content_parsed": 0, "no_call": 0, "timeouts": 0,
                 "server_parse_errors": 0, "loop_detected": False, "trimmed_turns": 0, "max_context_chars": 0, "latency_s": []}
        prompt = SYSTEM_PROMPT if self.engine is not None else LLM_ONLY_PROMPT
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": self._state(guard)}]
        state_idxs, calls, turns = [1], [], []  # state_idxs[k]: the state message sent after k calls

        def context(m):
            return self._context(m, state_idxs, calls, stats)

        for _ in range(self.max_steps):
            got = self._next_call(messages, stats, context, turns=turns)
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
                return self._submitted(args, guard, stats, turns)

            res = guard.call(name, args)
            if self.engine is not None:
                if res["status"] == "ok":
                    self.engine.update(name, res["args"], res["data"])
                elif res["status"] != "rejected":
                    self.engine.mark_failed(name, res["args"])
            last = {k: res.get(k) for k in ("tool", "args", "status", "data", "error", "reason")}
            calls.append(last)
            if call_id:
                messages.append({"role": "tool", "tool_call_id": call_id, "content": json.dumps(last)})
            messages.append({"role": "user", "content": self._state(guard, last)})
            state_idxs.append(len(messages) - 1)

            recent = calls[-LOOP_REPEATS:]
            if (len(recent) == LOOP_REPEATS and all((c["tool"], c["args"]) == (recent[0]["tool"], recent[0]["args"])
                                                    for c in recent)
                    and all(_no_new_info(recent[0], c) for c in recent[1:])):
                stats["loop_detected"] = True
                log.warning("loop detected: %s %s %d times with no new information; ending the session",
                            name, res["args"], LOOP_REPEATS)
                messages.append({"role": "user", "content": (
                    f"Loop detected: the same call {LOOP_REPEATS} times in a row gave no new information. "
                    "The session is ending. Call submit_diagnosis now with your best diagnosis.")})
                got = self._next_call(messages, stats, context, schemas={"submit_diagnosis": SUBMIT_SCHEMA},
                                      turns=turns)
                if got is not None:
                    return self._submitted(got[1], guard, stats, turns)
                break

        log.warning("agent ended without submitting a diagnosis")
        if self.engine is not None:
            d = self.engine.diagnosis()
            return {"method": self.method, **d, "status": "inconclusive" if d["status"] == "diagnosed" else d["status"],
                    "turns": turns, "agent_stats": stats}
        return {"method": self.method, "status": "inconclusive", "elements": [], "turns": turns, "agent_stats": stats}
