"""Distilled guard: static embeddings + logistic head, ~0.1ms per decision.

Trained by tools/fastpath_train.py with Laya as the teacher. This is a
pre-filter only, Laya still decides anything the student is not sure about.
"""
import time
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_ARTIFACT = Path(__file__).resolve().parents[2] / "guard/models/fastguard.joblib"


@dataclass
class FastVerdict:
    probability: float      # P(block)
    blocked: bool           # probability >= threshold
    latency_us: float
    model: str = "fastpath(model2vec+logreg)"

    def to_dict(self):
        return asdict(self)


class FastGuard:
    def __init__(self, artifact=None, threshold=0.5):
        import joblib
        from model2vec import StaticModel

        self.path = Path(artifact or DEFAULT_ARTIFACT)
        if not self.path.is_file():
            raise FileNotFoundError("no artifact at %s, run tools/fastpath_train.py" % self.path)
        bundle = joblib.load(self.path)
        self.classifier = bundle["classifier"]
        self.model = StaticModel.from_pretrained(bundle["embedding_model"])
        self.threshold = float(threshold)
        self.trained_at = bundle.get("trained_at")
        self.metrics = bundle.get("metrics", {})

    @staticmethod
    def available(artifact=None):
        return Path(artifact or DEFAULT_ARTIFACT).is_file()

    def score(self, text):
        t0 = time.perf_counter()
        emb = self.model.encode([text or ""])
        p = float(self.classifier.predict_proba(emb)[0][1])
        return FastVerdict(
            probability=round(p, 4),
            blocked=p >= self.threshold,
            latency_us=round((time.perf_counter() - t0) * 1e6, 1),
        )
