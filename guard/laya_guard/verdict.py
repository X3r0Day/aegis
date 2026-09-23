"""The result of one guard check."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


@dataclass
class Verdict:
    """Everything the guard decided, with enough context to log or display it.

    `decision` is the model's determination. `enforced` says whether the
    middleware actually dropped the request (block mode) or only flagged it
    (monitor mode).
    """

    decision: str                      # "block" | "allow"
    blocked: bool
    enforced: bool
    mode: str                          # "block" | "monitor"
    threshold: float                   # limit that fired (or the primary limit)
    thresholds: Dict[str, float]       # per-question limits
    probabilities: Dict[str, float]    # P(true) for each watched question
    triggers: List[Dict[str, Any]]     # watched questions that crossed the threshold
    confidences: Dict[str, float]      # per-question confidence
    model: str                         # checkpoint that answered
    routing: Optional[Dict[str, Any]]  # full routing record (auto mode)
    latency_ms: float
    text_sha256: str
    scope: Optional[str] = None        # how the scanned text was extracted
    excerpt: Optional[str] = None      # only when the config asks for it
    error: Optional[str] = None        # set when the check failed
    note: Optional[str] = None         # e.g. a rule override applied on top of Laya

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["label"] = self.label
        return data

    @property
    def label(self) -> str:
        if self.error:
            return "error"
        if self.blocked:
            return "block" if self.enforced else "would-block"
        return "allow"
