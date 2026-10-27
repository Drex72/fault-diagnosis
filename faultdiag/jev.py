"""Jev (TypeSafe AI's decision model) as the planner for methods D and E.

Jev does not write text or tool calls: it answers typed questions about a state. Here it
answers `choice` questions (pick one option; a probability for each). JevLLM puts it behind
the interface Agent expects from a chat model, so the agent loop, the guard, the engine
fallback and loop detection are unchanged:

- the conversation so far (bench description, every report and every action) is the state;
- question 1 lists every concrete call the tool schemas allow (enums expanded, the default
  pulse width, unordered probe pairs) plus submitting the diagnosis;
- when Jev picks submitting, question 2 lists the diagnoses: healthy, any one element or any
  two, from the same element vocabulary the chat models submit from.

A cloud service: it needs the internet and an API key in TYPESAFE_API_KEY (set it yourself;
it is never written to a record or a log). API: POST {base_url}/systemone, documented at
https://docs.typesafe.ai/api (read on 27 Oct 2026).
"""
from __future__ import annotations

import itertools
import json
import os
import time
import urllib.error
import urllib.request

from .agent import LLMBadOutput, LLMUnavailable
from .logsetup import get_logger
from .measure import DEFAULT_PULSE_MS
from .tools import ELEMENTS

log = get_logger("jev")

DEFAULT_URL = "https://api.typesafe.ai/v1"
KEY_ENV = "TYPESAFE_API_KEY"
MAX_OPTIONS = 255           # Jev's limit for one choice question
CHECK_TIMEOUT_S = 15        # the key check before a run
RETRY_STATUS = {429, 500, 502, 503, 524, 529}  # rate limit or a busy service: wait and ask again
SUBMIT = "submit_diagnosis"
HEALTHY = "healthy (no faulty element)"

NEXT_QUESTION = ("Which action should come next in this diagnosis? Choose 'submit_diagnosis' once the "
                 "readings identify the faulty elements or show that the bench is healthy.")
DIAGNOSIS_QUESTION = "Given every reading so far, which element or elements of the bench are faulty?"


def api_key() -> str:
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        raise ValueError(f"set {KEY_ENV} to your TypeSafe API key (or an OpenRouter key with "
                         "--base-url https://openrouter.ai/api/v1) to use Jev")
    return key


def action_options(functions: dict) -> dict:
    """label -> (tool, args, description) for every concrete call the tool schemas allow."""
    out = {}
    for name, fn in functions.items():
        if name == SUBMIT:
            continue
        props = fn["parameters"].get("properties", {})
        if name == "load_test":
            combos = [{"mode": "pulse", "width_ms": DEFAULT_PULSE_MS}, {"mode": "steady"}]
        elif name == "human_probe":  # a meter reading between a and b is the reading between b and a
            combos = [{"point_a": a, "point_b": b} for a, b in itertools.combinations(props["point_a"]["enum"], 2)]
        else:
            req = fn["parameters"].get("required", [])
            combos = [dict(zip(req, vals)) for vals in itertools.product(*(props[k]["enum"] for k in req))]
        for args in combos:
            label = " ".join([name, *(str(v) for v in args.values())])
            out[label] = (name, args, f"{fn['description']} Arguments: {json.dumps(args)}.")
    if SUBMIT in functions:
        out[SUBMIT] = (SUBMIT, None, "Stop measuring and give the diagnosis.")
    if len(out) > MAX_OPTIONS:
        raise ValueError(f"{len(out)} actions is more than Jev's {MAX_OPTIONS} options")
    return out


def diagnosis_options() -> dict:
    """label -> elements: healthy, any one element or any two. Names only, as the chat models get."""
    opts = {HEALTHY: []}
    opts.update({e: [e] for e in ELEMENTS})
    opts.update({f"{a} + {b}": [a, b] for a, b in itertools.combinations(ELEMENTS, 2)})
    if len(opts) > MAX_OPTIONS:
        raise ValueError(f"{len(opts)} diagnoses is more than Jev's {MAX_OPTIONS} options")
    return opts


def render_state(messages: list[dict]) -> dict:
    """The conversation as Jev's state: the bench description, then each report and action.
    Tool-result messages are skipped: the report that follows repeats the result."""
    steps = []
    for m in messages[1:]:
        if m["role"] == "user":
            steps.append("Report: " + m["content"])
        elif m["role"] == "assistant" and m.get("tool_calls"):
            fn = m["tool_calls"][0]["function"]
            steps.append(f"Action: {fn['name']} {fn['arguments']}")
    return {"bench": messages[0]["content"], "session": steps}


def _top(ans: dict, n: int = 3) -> str:
    probs = sorted((ans.get("probabilities") or {}).items(), key=lambda kv: -kv[1])[:n]
    return "; ".join(f"{k} ({v:.2f})" for k, v in probs) or ans["choice"]


def _retry_after(exc, default: float) -> float:
    """Seconds to wait from a Retry-After header (seconds form), capped at a minute."""
    try:
        return min(60.0, float((exc.headers or {}).get("Retry-After")))
    except (TypeError, ValueError):
        return default


def _strings(obj) -> set:
    """Model names in a /models reply, whatever its exact shape."""
    if isinstance(obj, str):
        return {obj}
    if isinstance(obj, list):
        return set().union(*(_strings(x) for x in obj)) if obj else set()
    if isinstance(obj, dict):
        return set().union(*(_strings(v) for k, v in obj.items() if k in ("id", "name", "model", "data", "models")))
    return set()


class JevLLM:
    def __init__(self, base_url: str, model: str, key: str, timeout_s: float = 60, max_retries: int = 3):
        self.base = base_url.rstrip("/")
        self.url = self.base + "/systemone"
        self.model, self.timeout_s, self.max_retries = model, timeout_s, max_retries
        self._headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        self.usage = {"requests": 0, "input_tokens": 0}
        self.answered_by = None
        self._ids = itertools.count(1)

    def check(self) -> set:
        """Before a run: the key works and the service answers. Returns the model names listed."""
        req = urllib.request.Request(self.base + "/models", headers=self._headers)
        log.info("checking the API key and the Jev service at %s (up to %d s)", self.base, CHECK_TIMEOUT_S)
        try:
            with urllib.request.urlopen(req, timeout=CHECK_TIMEOUT_S) as resp:
                names = _strings(json.loads(resp.read()))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):  # TypeSafe answers 403 "authentication_error" for a missing or bad key
                detail = exc.read()[:300].decode("utf-8", "replace")
                raise ValueError(f"Jev refused the API key (HTTP {exc.code}); check {KEY_ENV}: {detail}") from exc
            log.warning("could not list Jev models (HTTP %d); going on", exc.code)
            return set()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ValueError(f"cannot reach Jev at {self.base} within {CHECK_TIMEOUT_S} s (internet needed): {exc}") from exc
        if names and self.model not in names:
            log.warning("model %s is not in this key's list %s; aliases may not be listed", self.model, sorted(names))
        log.info("Jev answered: key accepted")
        return names

    def __call__(self, messages: list[dict], tools: list[dict]) -> dict:
        actions = action_options({t["function"]["name"]: t["function"] for t in tools})
        if not actions:
            raise ValueError("Jev needs at least one tool to choose from")
        state = render_state(messages)
        picked, note = SUBMIT, ""
        if set(actions) != {SUBMIT}:  # the loop-detection turn offers only submitting
            ans = self._choose(state, "next_action", NEXT_QUESTION, {k: v[2] for k, v in actions.items()})
            picked, note = ans["choice"], _top(ans)
        if picked != SUBMIT:
            tool, args, _ = actions[picked]
            return self._reply(tool, args, f"Jev chose {picked}. Top options: {note}")
        diag = diagnosis_options()
        ans = self._choose(state, "diagnosis", DIAGNOSIS_QUESTION, dict.fromkeys(diag))
        conf = ans.get("confidence")
        if conf is None:
            conf = (ans.get("probabilities") or {}).get(ans["choice"], 0.0)
        args = {"faults": [{"element": e} for e in diag[ans["choice"]]], "confidence": min(1.0, max(0.0, float(conf)))}
        return self._reply(SUBMIT, args, f"Jev diagnosed {ans['choice']}. Top options: {_top(ans)}")

    def _reply(self, tool: str, args: dict, content: str) -> dict:
        return {"role": "assistant", "content": content,
                "tool_calls": [{"id": f"jev-{next(self._ids)}", "type": "function",
                                "function": {"name": tool, "arguments": json.dumps(args)}}]}

    def _choose(self, state: dict, name: str, instructions: str, criteria: dict) -> dict:
        out = self._post({"model": self.model, "state": state,
                          "questions": {name: {"type": "choice", "instructions": instructions, "criteria": criteria}}})
        try:
            ans = out["answers"][name]
            choice = ans["choice"]
        except (KeyError, TypeError) as exc:
            raise LLMBadOutput(f"Jev's reply has no answer to {name}: {json.dumps(out)[:300]}") from exc
        if choice not in criteria:
            raise LLMBadOutput(f"Jev chose {choice!r}, which was not one of the options for {name}")
        return ans

    def _post(self, body: dict) -> dict:
        data = json.dumps(body).encode()
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(self.url, data, self._headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    out = json.loads(resp.read())
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read()[:300].decode("utf-8", "replace")
                if exc.code in RETRY_STATUS and attempt < self.max_retries:
                    wait = _retry_after(exc, 2 ** attempt)
                    log.warning("Jev returned HTTP %d (attempt %d); asking again in %.0f s: %s",
                                exc.code, attempt + 1, wait, detail)
                    time.sleep(wait)
                    continue
                hint = {401: f"; check {KEY_ENV}", 402: "; out of credits",
                        403: "; the key may not have access to this model"}.get(exc.code, "")
                raise LLMUnavailable(f"{self.url} returned HTTP {exc.code}{hint}: {detail}") from exc
            except urllib.error.URLError as exc:
                if isinstance(exc.reason, TimeoutError):
                    raise TimeoutError(f"no reply from {self.model} within {self.timeout_s} s") from exc
                raise LLMUnavailable(f"cannot reach Jev at {self.url} (internet needed): {exc.reason}") from exc
            except TimeoutError as exc:
                raise TimeoutError(f"no reply from {self.model} within {self.timeout_s} s") from exc
        self.usage["requests"] += 1
        self.usage["input_tokens"] += int((out.get("usage") or {}).get("input_tokens") or 0)
        log.debug("Jev request %d: %d bytes sent, %s", self.usage["requests"], len(data), out.get("usage"))
        if out.get("model") and out["model"] != self.answered_by:
            if out["model"] != self.model:
                log.warning("asked for %s, answered by %s", self.model, out["model"])
            self.answered_by = out["model"]
        return out
