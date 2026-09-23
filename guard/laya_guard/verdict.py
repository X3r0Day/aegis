from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class Verdict:
    decision: str                      # block | allow
    blocked: bool
    enforced: bool                     # what the middleware actually did
    mode: str                          # block | monitor
    threshold: float                   # the limit that fired
    thresholds: dict[str, float]       # per question
    probabilities: dict[str, float]    # P(true), watched questions
    triggers: list[dict[str, Any]]     # watched questions over their limit
    confidences: dict[str, float]
    model: str                         # checkpoint that answered
    routing: dict[str, Any] | None
    latency_ms: float
    text_sha256: str
    scope: str | None = None           # how the text was picked out
    excerpt: str | None = None         # only if the config stores one
    error: str | None = None
    note: str | None = None            # rule override on top of Laya, if any

    @property
    def label(self) -> str:
        if self.error:
            return "error"
        if self.blocked:
            return "block" if self.enforced else "would-block"
        return "allow"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["label"] = self.label
        return d
