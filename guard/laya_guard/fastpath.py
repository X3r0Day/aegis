"""Sub-millisecond fast path: Laya distilled into static embeddings + logistic head.

The student is trained by `guard/tools/fastpath_train.py` — Laya (english) labels
generated prompt variants, Model2Vec encodes them, a logistic regression learns
the teacher's block/allow boundary. Decisions cost ~0.1 ms on CPU, so it can
sit inline on every request; Laya remains the authority (teacher + fallback for
anything the student is not confident about).
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_ARTIFACT = Path(__file__).resolve().parents[2] / "guard" / "models" / "fastguard.joblib"


@dataclass
class FastVerdict:
    probability: float          # P(block) from the student
    blocked: bool               # probability >= threshold
    latency_us: float
    model: str = "fastpath(model2vec+logreg)"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class FastGuard:
    """Load the distilled student and score text in microseconds."""

    def __init__(self, artifact: Optional[Path] = None, threshold: float = 0.5):
        import joblib
        from model2vec import StaticModel

        self.path = Path(artifact or DEFAULT_ARTIFACT)
        if not self.path.is_file():
            raise FileNotFoundError("no fast-path artifact at %s — run guard/tools/fastpath_train.py" % self.path)
        bundle = joblib.load(self.path)
        self.classifier = bundle["classifier"]
        self.model = StaticModel.from_pretrained(bundle["embedding_model"])
        self.threshold = float(threshold)
        self.trained_at = bundle.get("trained_at")
        self.metrics = bundle.get("metrics", {})

    @staticmethod
    def available(artifact: Optional[Path] = None) -> bool:
        return Path(artifact or DEFAULT_ARTIFACT).is_file()

    def score(self, text: str) -> FastVerdict:
        started = time.perf_counter()
        embedding = self.model.encode([text or ""])
        probability = float(self.classifier.predict_proba(embedding)[0][1])
        latency = (time.perf_counter() - started) * 1e6
        return FastVerdict(probability=round(probability, 4), blocked=probability >= self.threshold,
                           latency_us=round(latency, 1))
