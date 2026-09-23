"""Which Laya checkpoints exist locally, and which this host can execute.

The guard can switch models per request; this module is the single source of
truth for what that dropdown offers. `laya-coreml` is listed for completeness:
it is the multilingual checkpoint converted to CoreML for Apple Silicon
(FluidInference/laya-coreml, runs via FluidUse on macOS 14+) — the same weights
run here through the ordinary `multilingual` entry.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import DEFAULT_MODELS_DIR

KNOWN_MODELS = ("english", "multilingual", "typed-decisions")


@dataclass
class ModelEntry:
    id: str
    name: str
    kind: str                      # transformers | coreml
    runnable: bool                 # this host can execute it with our runtime
    available: bool                # files present on disk
    default_for: List[str] = field(default_factory=list)   # "guard" / "abuse"
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _has_checkpoint(models_dir: Path, subfolder: Optional[str]) -> bool:
    root = models_dir / subfolder if subfolder else models_dir
    return (root / "model.safetensors").is_file()


def model_registry(models_dir: Optional[Path] = None) -> List[ModelEntry]:
    models_dir = Path(models_dir or DEFAULT_MODELS_DIR)
    return [
        ModelEntry(
            id="english",
            name="Laya English (base)",
            kind="transformers",
            runnable=_has_checkpoint(models_dir, None),
            available=_has_checkpoint(models_dir, None),
            default_for=["guard"],
            note="ModernBERT-large, 512 ctx. The guard's tuned questions were validated on this checkpoint.",
        ),
        ModelEntry(
            id="multilingual",
            name="Laya Multilingual (base)",
            kind="transformers",
            runnable=_has_checkpoint(models_dir, "multilingual"),
            available=_has_checkpoint(models_dir, "multilingual"),
            note="mmBERT-base, 100+ languages. Same weights as the CoreML conversion; "
                 "not validated for the tuned guard/abuse questions.",
        ),
        ModelEntry(
            id="typed-decisions",
            name="Laya Typed-Decisions (fine-tune)",
            kind="transformers",
            runnable=_has_checkpoint(models_dir, "typed-decisions"),
            available=_has_checkpoint(models_dir, "typed-decisions"),
            default_for=["abuse"],
            note="Security-incident fine-tune; calibrated for abuse decisions (threshold 0.45).",
        ),
        ModelEntry(
            id="laya-coreml",
            name="Laya CoreML (multilingual · ANE)",
            kind="coreml",
            runnable=False,
            available=False,
            note="FluidInference/laya-coreml: the multilingual checkpoint as CoreML, "
                 "runs via FluidUse on macOS 14+ (Apple Silicon). Cannot execute on this host — "
                 "use the 'multilingual' entry here for the identical weights.",
        ),
    ]


def entry_for(model_id: str, models_dir: Optional[Path] = None) -> Optional[ModelEntry]:
    for entry in model_registry(models_dir):
        if entry.id == model_id:
            return entry
    return None


def runnable_ids(models_dir: Optional[Path] = None) -> List[str]:
    return [e.id for e in model_registry(models_dir) if e.runnable and e.kind == "transformers"]
