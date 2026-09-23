from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import DEFAULT_MODELS_DIR

KNOWN_MODELS = ("english", "multilingual", "typed-decisions")


@dataclass
class ModelEntry:
    id: str
    name: str
    kind: str                    # transformers | coreml
    runnable: bool               # can this host run it
    available: bool              # weights on disk
    default_for: list = field(default_factory=list)
    note: str = ""

    def to_dict(self):
        return asdict(self)


def _has(models_dir, sub):
    root = models_dir / sub if sub else models_dir
    return (root / "model.safetensors").is_file()


def model_registry(models_dir=None):
    models_dir = Path(models_dir or DEFAULT_MODELS_DIR)
    return [
        ModelEntry(
            "english", "Laya English (base)", "transformers",
            _has(models_dir, None), _has(models_dir, None),
            ["guard"], "ModernBERT-large, 512 ctx. guard questions were tuned on this one.",
        ),
        ModelEntry(
            "multilingual", "Laya Multilingual (base)", "transformers",
            _has(models_dir, "multilingual"), _has(models_dir, "multilingual"),
            [], "mmBERT-base, 100+ languages. same weights as the coreml build, "
                "not validated for the tuned questions.",
        ),
        ModelEntry(
            "typed-decisions", "Laya Typed-Decisions (fine-tune)", "transformers",
            _has(models_dir, "typed-decisions"), _has(models_dir, "typed-decisions"),
            ["abuse"], "security-incident fine-tune, abuse threshold 0.45 was calibrated on it.",
        ),
        ModelEntry(
            "laya-coreml", "Laya CoreML (multilingual, ANE)", "coreml",
            False, False, [],
            "FluidInference/laya-coreml, runs via FluidUse on macOS 14+. not runnable here, "
            "use the multilingual entry for the same weights.",
        ),
    ]


def entry_for(model_id, models_dir=None):
    for entry in model_registry(models_dir):
        if entry.id == model_id:
            return entry
    return None


def runnable_ids(models_dir=None):
    return [e.id for e in model_registry(models_dir) if e.runnable and e.kind == "transformers"]
