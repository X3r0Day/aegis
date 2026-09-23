"""Append-only JSONL log of guard decisions (thread-safe)."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol


class VerdictLike(Protocol):
    """Anything with a `to_dict()` — Verdict and AbuseVerdict both qualify."""

    def to_dict(self) -> Dict[str, Any]: ...


class DecisionLog:
    """One JSON object per line; `tail()` powers the dashboard/chat sidebar."""

    def __init__(self, path: Path, default_limit: int = 100):
        self.path = Path(path)
        self.default_limit = default_limit
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, verdict: VerdictLike, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        record: Dict[str, Any] = {
            "ts": time.time(),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
            **verdict.to_dict(),
        }
        if meta:
            record["meta"] = meta
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return record

    def tail(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Newest `limit` records, oldest first. Corrupt lines are skipped."""
        limit = max(1, min(int(limit or self.default_limit), 1000))
        if not self.path.exists():
            return []
        with self._lock:
            with self.path.open("r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()[-limit:]
        records = []
        for line in lines:
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
        return records
