"""Provenance stamped on every result record: source, commit, policy hash, time."""
from __future__ import annotations

import functools
import hashlib
import json
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
LAGOS = ZoneInfo("Africa/Lagos")
SOURCES = ("sim", "hardware")


def lagos_now() -> str:
    return datetime.now(LAGOS).isoformat(timespec="seconds")


@functools.cache
def git_commit() -> str:
    """Commit hash of the code this process loaded, with '+dirty' if tracked files had
    uncommitted changes. Taken once, when this module is imported: a long run keeps running
    the code it started with even if a commit lands while it runs."""
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout.strip()
        return head + ("+dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


git_commit()  # stamp the code as loaded, before any long run starts


def policy_hash(policy: dict) -> str:
    """Short hash of the policy as loaded (key order does not matter)."""
    return hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()[:12]


def ollama_model_version(base_url: str, model: str) -> str | None:
    """Digest of a model on an Ollama server, or None if it cannot be looked up
    (for example llama.cpp, which has no /api/tags)."""
    root = base_url.rstrip("/").removesuffix("/v1")
    try:
        with urllib.request.urlopen(root + "/api/tags", timeout=3) as resp:
            tags = json.loads(resp.read())
    except (OSError, ValueError):
        return None
    for m in tags.get("models", []):
        if m.get("name") == model or m.get("model") == model:
            return m.get("digest", "")[:12] or None
    return None


def run_meta(source: str, policy: dict, **extra) -> dict:
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}, got {source!r}")
    return {"source": source, "commit": git_commit(), "policy_hash": policy_hash(policy), **extra}
