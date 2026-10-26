"""Policy guard: every tool call from any method goes through here.

It validates arguments, enforces the cost budget and per-session limits, and
logs each call. Safety limits are enforced again by the bench itself (the
ESP32 on hardware), so the guard is never the only protection.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .logsetup import get_logger
from .tools import validate_call

log = get_logger("guard")
DEFAULT_POLICY = Path(__file__).resolve().parent.parent / "config" / "policy.json"


def load_policy(path: Path | str = DEFAULT_POLICY) -> dict:
    return json.loads(Path(path).read_text())


class PolicyGuard:
    def __init__(self, bench, policy: dict | None = None):
        self.bench = bench
        self.policy = policy or load_policy()
        self.spent = 0
        self.counts: Counter = Counter()
        self.history: list[dict] = []
        self.rejections: list[dict] = []

    @property
    def remaining(self) -> int:
        return self.policy["budget"] - self.spent

    def cost_of(self, tool: str, args: dict) -> int:
        key = f"load_test:{args.get('mode')}" if tool == "load_test" else tool
        return self.policy["costs"][key]

    def check(self, tool: str, args: dict) -> str | None:
        """Reason the call is not allowed now, or None."""
        errors = validate_call(tool, args)
        if errors:
            return "; ".join(errors)
        limit = self.policy["max_per_session"].get(tool)
        if limit is not None and self.counts[tool] >= limit:
            return f"{tool} limit of {limit} per session reached"
        if self.cost_of(tool, args) > self.remaining:
            return f"cost {self.cost_of(tool, args)} exceeds remaining budget {self.remaining}"
        if tool == "load_test":
            lt = self.policy["load_test"]
            if not lt["width_ms_min"] <= args.get("width_ms", lt["width_ms_default"]) <= lt["width_ms_max"]:
                return "width_ms outside policy bounds"
        if tool == "human_probe" and args.get("point_a") == args.get("point_b"):
            return "point_a and point_b must differ"
        return None

    def call(self, tool: str, args: dict | None = None) -> dict:
        args = dict(args or {})
        if tool == "load_test" and args.get("mode") == "pulse":
            args.setdefault("width_ms", self.policy["load_test"]["width_ms_default"])
        reason = self.check(tool, args)
        if reason:
            entry = {"tool": tool, "args": args, "status": "rejected", "reason": reason, "cost": 0}
            self.rejections.append(entry)
            log.warning("rejected %s %s: %s", tool, args, reason)
            return entry
        if tool == "get_history":
            return {"tool": tool, "args": args, "status": "ok", "cost": 0,
                    "data": {"history": [{k: e[k] for k in ("tool", "args", "status", "data")} for e in self.history]}}

        result = self.bench.execute(tool, args)
        cost = self.cost_of(tool, args)  # charged even on timeout: the time was spent
        self.spent += cost
        self.counts[tool] += 1
        entry = {"step": len(self.history) + 1, "tool": tool, "args": args, "status": result["status"],
                 "data": result.get("data"), "error": result.get("error"), "cost": cost, "spent": self.spent}
        self.history.append(entry)
        if result["status"] == "ok":
            log.debug("step %d %s %s -> %s (spent %d)", entry["step"], tool, args, entry["data"], self.spent)
        else:
            log.warning("step %d %s %s -> %s: %s", entry["step"], tool, args, result["status"], result.get("error"))
        return entry
