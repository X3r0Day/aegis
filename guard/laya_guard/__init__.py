"""laya_guard — a prompt-injection firewall built on the Laya System 1 models.

    from laya_guard import GuardConfig, LayaGuard

    guard = LayaGuard(GuardConfig(threshold=0.8)).warm()
    verdict = guard.check_messages([{"role": "user", "content": "Ignore all previous instructions..."}])
    if verdict.enforced:
        drop_the_request()
"""
from .abuse import AbuseConfig, AbuseDetector, AbuseVerdict, TrafficAggregator, abuse_questions
from .config import DEFAULT_MODELS_DIR, GuardConfig, default_questions
from .fastpath import FastGuard, FastVerdict
from .guard import LayaGuard, extract_scan_text
from .logging import DecisionLog
from .registry import KNOWN_MODELS, ModelEntry, model_registry, runnable_ids
from .rules import rule_decision
from .verdict import Verdict

__all__ = [
    "GuardConfig",
    "LayaGuard",
    "DecisionLog",
    "Verdict",
    "extract_scan_text",
    "default_questions",
    "DEFAULT_MODELS_DIR",
    "AbuseConfig",
    "AbuseDetector",
    "AbuseVerdict",
    "TrafficAggregator",
    "abuse_questions",
    "FastGuard",
    "FastVerdict",
    "ModelEntry",
    "KNOWN_MODELS",
    "model_registry",
    "runnable_ids",
    "rule_decision",
]

__version__ = "0.1.0"
