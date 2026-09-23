import json
import threading
import time
from pathlib import Path


class DecisionLog:
    def __init__(self, path, default_limit=100):
        self.path = Path(path)
        self.default_limit = default_limit
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # verdict is anything with to_dict(): Verdict or AbuseVerdict
    def append(self, verdict, meta=None):
        rec = {
            "ts": time.time(),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
            **verdict.to_dict(),
        }
        if meta:
            rec["meta"] = meta
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def tail(self, limit=None):
        # newest N first-written last, so the UI can read top down. a partial
        # line from a crash mid-write just gets skipped
        limit = max(1, min(int(limit or self.default_limit), 1000))
        try:
            with self._lock:
                with self.path.open("r", encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()[-limit:]
        except FileNotFoundError:
            return []

        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out
