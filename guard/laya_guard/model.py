"""Shared Laya runtime: one Router per process, used by every guard component.

The checkpoints are ~2 GB in RAM when both are loaded; loading a second copy
for the abuse detector would double that for no reason.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional

_runner = None
_lock = threading.Lock()


def get_runner(models_dir, device: Optional[str] = None):
    """Return the process-wide `laya.Router` pointed at the local checkpoints."""
    global _runner
    with _lock:
        if _runner is None:
            from laya import Router

            models: Dict[str, Any] = {
                "english": (str(models_dir), None),
                "multilingual": (str(models_dir), "multilingual"),
                "typed-decisions": (str(models_dir), "typed-decisions"),
            }
            _runner = Router(models=models, device=device, max_loaded=2, default="english")
        return _runner
