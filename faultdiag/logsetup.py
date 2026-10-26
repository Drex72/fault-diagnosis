"""Terminal logging. Set FAULTDIAG_LOG=DEBUG to see every tool call."""
from __future__ import annotations

import logging
import os
import sys

_configured = False


def get_logger(name: str) -> logging.Logger:
    global _configured
    if not _configured:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
        root = logging.getLogger("faultdiag")
        root.addHandler(handler)
        root.setLevel(os.environ.get("FAULTDIAG_LOG", "INFO").upper())
        root.propagate = False
        _configured = True
    return logging.getLogger(f"faultdiag.{name}")
