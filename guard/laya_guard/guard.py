"""LayaGuard: decides whether a piece of text is a prompt-injection attempt.

The guard wraps the local Laya checkpoints (through `laya.Router`, so
non-English input is routed to the multilingual checkpoint automatically) and
turns one forward pass over the guard questions into a :class:`Verdict`.
"""
from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, cast

from .config import GuardConfig
from .model import get_runner
from .rules import RULE_REASONS, rule_decision
from .verdict import Verdict


def _content_text(content: Any) -> str:
    """Flatten an OpenAI-style message `content` (string or typed parts) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: List[str] = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        return "\n".join(chunks)
    return ""


def extract_scan_text(messages: Any, scope: str = "latest_user") -> Tuple[str, str]:
    """Pick the text to scan out of an OpenAI-style `messages` list.

    Returns (text, scope_used). `latest_user` scans the newest user message —
    the window Laya keeps when it has to truncate, so the newest request is
    never the part that falls off. `all_user` joins every user message;
    `all` joins every message role-tagged.
    """
    msgs = [m for m in (messages or []) if isinstance(m, dict)]

    if scope == "all_user":
        picked = [m for m in msgs if str(m.get("role") or "").lower() == "user"]
        return "\n\n".join(_content_text(m.get("content")) for m in picked), "all_user"

    if scope == "all":
        joined = "\n\n".join(
            "[%s] %s" % (m.get("role", "?"), _content_text(m.get("content"))) for m in msgs
        )
        return joined, "all"

    # latest_user, with a fallback to the newest message of any role
    for message in reversed(msgs):
        if str(message.get("role") or "").lower() == "user":
            text = _content_text(message.get("content"))
            if text.strip():
                return text, "latest_user"
    for message in reversed(msgs):
        text = _content_text(message.get("content"))
        if text.strip():
            return text, "fallback_any"
    return "", "empty"


class LayaGuard:
    """Programmatic prompt-injection firewall around the Laya checkpoints."""

    def __init__(self, config: Optional[GuardConfig] = None):
        self.cfg = config or GuardConfig()
        self._runner = None            # laya.Router, built on first use
        self._load_lock = threading.Lock()
        self._infer_lock = threading.Lock()   # one forward pass at a time (CPU)

    # ---------------------------------------------------------------- loading
    def _ensure_runner(self):
        with self._load_lock:
            if self._runner is None:
                self._runner = get_runner(self.cfg.models_dir, self.cfg.device)
            return self._runner

    def warm(self) -> "LayaGuard":
        """Load and warm the default checkpoint (first call is otherwise slow)."""
        runner = self._ensure_runner()
        agent = runner.load("english")
        agent.predict(
            {"warmup": True},
            {"ready": {"type": "noul", "instructions": "Is the model warm?"}},
        )
        return self

    @property
    def loaded_models(self) -> List[str]:
        return self._runner.loaded if self._runner is not None else []

    # --------------------------------------------------------------- checking
    def check(self, text: str, *, mode: Optional[str] = None, scope: str = "text") -> Verdict:
        """Run one guard pass over `text` and apply the configured policy."""
        cfg = self.cfg
        mode = mode or cfg.mode
        raw = text or ""
        digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
        scanned = raw[: cfg.max_chars]
        started = time.perf_counter()

        try:
            runner = self._ensure_runner()
            with self._infer_lock:
                # The state must be a dict keyed "prompt" (the key the guard
                # questions refer to). A raw string state shifts the answers
                # badly: e.g. a benign Hindi request scored 0.999 as injection
                # raw vs 0.364 wrapped, while attacks stay above 0.92.
                result = runner.predict(
                    {"prompt": scanned},
                    cast(Dict[str, Any], cfg.questions),
                    model=None if cfg.model == "auto" else cfg.model,
                )
            answers = result["answers"]
            routing = result.get("routing") or {}
        except Exception as exc:  # noqa: BLE001 — every failure is a verdict, not a crash
            latency = (time.perf_counter() - started) * 1000.0
            blocked = not cfg.fail_open
            return Verdict(
                decision="block" if blocked else "allow",
                blocked=blocked,
                enforced=blocked and mode == "block",
                mode=mode,
                threshold=cfg.threshold,
                thresholds=dict(cfg.thresholds),
                probabilities={},
                triggers=[],
                confidences={},
                model=cfg.model,
                routing=None,
                latency_ms=round(latency, 1),
                text_sha256=digest,
                scope=scope,
                excerpt=scanned[: cfg.excerpt_chars] if cfg.excerpt_chars else None,
                error="%s: %s" % (type(exc).__name__, exc),
            )

        probabilities: Dict[str, float] = {}
        confidences: Dict[str, float] = {}
        triggers: List[Dict[str, Any]] = []
        for qid in cfg.watch:
            answer = answers.get(qid)
            if not answer:
                continue
            probability = float(answer.get("noul", answer.get("score", 0.0)))
            probabilities[qid] = round(probability, 4)
            confidences[qid] = round(float(answer.get("confidence", 0.0)), 4)
            limit = cfg.thresholds.get(qid, cfg.threshold)
            if probability >= limit:
                triggers.append({"question": qid, "probability": round(probability, 4), "threshold": limit})

        blocked = bool(triggers)

        # Deterministic overrides for the system-prompt blind spot (see rules.py):
        # extraction patterns block even when Laya shrugs; clearly benign authoring
        # requests are exempted even when Laya over-fires.
        note: Optional[str] = None
        rule = rule_decision(scanned)
        if rule and rule.startswith("block") and not blocked:
            blocked = True
            probabilities["rule_extraction"] = 1.0
            triggers.append({"question": "rule:system_prompt_extraction", "probability": 1.0, "threshold": 1.0})
            note = RULE_REASONS.get(rule, "rule override: block")
        elif rule and rule.startswith("allow") and blocked:
            blocked = False
            triggers = []
            probabilities["rule_authoring"] = 1.0
            note = RULE_REASONS.get(rule, "rule override: allow")

        applied_threshold = triggers[0]["threshold"] if triggers else cfg.thresholds.get("prompt_injection", cfg.threshold)
        latency = (time.perf_counter() - started) * 1000.0
        return Verdict(
            decision="block" if blocked else "allow",
            blocked=blocked,
            enforced=blocked and mode == "block",
            mode=mode,
            threshold=applied_threshold,
            thresholds=dict(cfg.thresholds),
            probabilities=probabilities,
            triggers=triggers,
            confidences=confidences,
            model=routing.get("model") or (cfg.model if cfg.model != "auto" else "english"),
            routing=routing or None,
            latency_ms=round(latency, 1),
            text_sha256=digest,
            scope=scope,
            excerpt=scanned[: cfg.excerpt_chars] if cfg.excerpt_chars else None,
            note=note,
        )

    def check_messages(self, messages: Any, *, mode: Optional[str] = None) -> Verdict:
        """Check the relevant text of an OpenAI-style `messages` payload."""
        text, scope = extract_scan_text(messages, self.cfg.scope)
        return self.check(text, mode=mode, scope=scope)

    def check_request(self, payload: Dict[str, Any], *, mode: Optional[str] = None) -> Verdict:
        """Check a full request body: uses `messages` when present, else `input`/`text`."""
        if isinstance(payload, dict):
            if payload.get("messages") is not None:
                return self.check_messages(payload.get("messages"), mode=mode)
            text = payload.get("input", payload.get("text", ""))
            if isinstance(text, str):
                return self.check(text, mode=mode, scope="input")
        return self.check(str(payload), mode=mode, scope="raw")
