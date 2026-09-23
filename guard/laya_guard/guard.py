"""Prompt-injection firewall around the local Laya checkpoints."""
import hashlib
import threading
import time
from typing import Any

from .config import GuardConfig
from .model import get_runner
from .rules import RULE_REASONS, rule_decision
from .verdict import Verdict


def _content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # openai-style content parts
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts)
    return ""


def extract_scan_text(messages, scope="latest_user"):
    msgs = [m for m in (messages or []) if isinstance(m, dict)]

    if scope == "all_user":
        picked = [m for m in msgs if str(m.get("role") or "").lower() == "user"]
        return "\n\n".join(_content_text(m.get("content")) for m in picked), "all_user"

    if scope == "all":
        joined = "\n\n".join(
            "[%s] %s" % (m.get("role", "?"), _content_text(m.get("content"))) for m in msgs
        )
        return joined, "all"

    # default: newest user message. Laya keeps the head of a state when it has
    # to truncate, so scanning a growing transcript would drop the new turn
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
    def __init__(self, config=None):
        self.cfg = config or GuardConfig()
        self._runner = None
        self._load_lock = threading.Lock()
        self._infer_lock = threading.Lock()   # cpu, one pass at a time

    def _ensure_runner(self):
        with self._load_lock:
            if self._runner is None:
                self._runner = get_runner(self.cfg.models_dir, self.cfg.device)
            return self._runner

    def warm(self):
        runner = self._ensure_runner()
        runner.load("english").predict(
            {"warmup": True},
            {"ready": {"type": "noul", "instructions": "Is the model warm?"}},
        )
        return self

    @property
    def loaded_models(self):
        return self._runner.loaded if self._runner is not None else []

    def check(self, text, *, mode=None, scope="text"):
        cfg = self.cfg
        mode = mode or cfg.mode
        raw = text or ""
        digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
        scanned = raw[:cfg.max_chars]
        started = time.perf_counter()

        try:
            with self._infer_lock:
                # state has to be a dict keyed "prompt", the key the questions
                # refer to. a raw string shifts everything: benign hindi read
                # 0.999 injection raw vs 0.364 wrapped while attacks stayed high
                result = self._ensure_runner().predict(
                    {"prompt": scanned},
                    cfg.questions,
                    model=None if cfg.model == "auto" else cfg.model,
                )
            answers = result["answers"]
            routing = result.get("routing") or {}
        except Exception as exc:  # a dead model is a verdict too, not a crash
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
                latency_ms=round((time.perf_counter() - started) * 1000.0, 1),
                text_sha256=digest,
                scope=scope,
                excerpt=scanned[:cfg.excerpt_chars] if cfg.excerpt_chars else None,
                error="%s: %s" % (type(exc).__name__, exc),
            )

        probabilities = {}
        confidences = {}
        triggers = []
        for qid in cfg.watch:
            answer = answers.get(qid)
            if not answer:
                continue
            p = float(answer.get("noul", answer.get("score", 0.0)))
            probabilities[qid] = round(p, 4)
            confidences[qid] = round(float(answer.get("confidence", 0.0)), 4)
            limit = cfg.thresholds.get(qid, cfg.threshold)
            if p >= limit:
                triggers.append({"question": qid, "probability": round(p, 4), "threshold": limit})

        blocked = bool(triggers)

        # rules.py handles two spots where the english checkpoint just gets it
        # wrong: system prompt extraction vs authoring, and greetings
        note = None
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

        threshold = triggers[0]["threshold"] if triggers else cfg.thresholds.get("prompt_injection", cfg.threshold)
        return Verdict(
            decision="block" if blocked else "allow",
            blocked=blocked,
            enforced=blocked and mode == "block",
            mode=mode,
            threshold=threshold,
            thresholds=dict(cfg.thresholds),
            probabilities=probabilities,
            triggers=triggers,
            confidences=confidences,
            model=routing.get("model") or (cfg.model if cfg.model != "auto" else "english"),
            routing=routing or None,
            latency_ms=round((time.perf_counter() - started) * 1000.0, 1),
            text_sha256=digest,
            scope=scope,
            excerpt=scanned[:cfg.excerpt_chars] if cfg.excerpt_chars else None,
            note=note,
        )

    def check_messages(self, messages, *, mode=None):
        text, scope = extract_scan_text(messages, self.cfg.scope)
        return self.check(text, mode=mode, scope=scope)

    def check_request(self, payload, *, mode=None):
        if isinstance(payload, dict):
            if payload.get("messages") is not None:
                return self.check_messages(payload.get("messages"), mode=mode)
            text = payload.get("input", payload.get("text", ""))
            if isinstance(text, str):
                return self.check(text, mode=mode, scope="input")
        return self.check(str(payload), mode=mode, scope="raw")
