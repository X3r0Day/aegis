#!/usr/bin/env python3
"""Bench the distilled fast path: agreement with Laya and per-decision latency.

    .venv/bin/python guard/tools/fastpath_bench.py

Requires guard/models/fastguard.joblib (run guard/tools/fastpath_train.py first).
"""
from __future__ import annotations

import os
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent          # guard/tools
SERVER = HERE.parent                            # guard
ROOT = SERVER.parent                            # binary/
sys.path.insert(0, str(SERVER))                 # laya_guard
sys.path.insert(0, str(HERE))                   # sibling tools

os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("HF_HOME", str(ROOT / "laya" / "models" / "hf"))

from laya_guard import FastGuard, GuardConfig, LayaGuard  # noqa: E402
import guard_eval  # noqa: E402


def main() -> int:
    if not FastGuard.available():
        print("no artifact — run guard/tools/fastpath_train.py first")
        return 1

    fast = FastGuard(threshold=0.5)
    print("student: %s | trained %s" % (fast.model.__class__.__name__, fast.trained_at))
    if fast.metrics:
        print("training metrics:", {k: v for k, v in fast.metrics.items() if k != "holdout"})

    guard = LayaGuard(GuardConfig(model="english")).warm()
    print("\n%-6s %-7s %-9s %-7s | %s" % ("expect", "laya", "student", "p", "prompt"))
    agree = 0
    for expected, text in guard_eval.SAMPLES:
        teacher = guard.check(text)
        student = fast.score(text)
        student_decision = "block" if student.blocked else "allow"
        teacher_decision = "block" if teacher.blocked else "allow"
        agree += teacher_decision == student_decision
        print("%-6s %-7s %-9s %-7.3f | %s" % (
            expected, teacher_decision, student_decision, student.probability, text[:52]))
    print("\nstudent agrees with Laya on %d/%d guard_eval samples" % (agree, len(guard_eval.SAMPLES)))

    texts = [text for _, text in guard_eval.SAMPLES]
    for _ in range(50):
        fast.score(texts[0])
    n = 1000
    samples = []
    started = time.perf_counter()
    for i in range(n):
        t0 = time.perf_counter()
        fast.score(texts[i % len(texts)])
        samples.append((time.perf_counter() - t0) * 1e6)
    total_us = (time.perf_counter() - started) / n * 1e6
    samples.sort()
    print("latency single: mean %.0f us | p50 %.0f us | p95 %.0f us (n=%d)" % (
        total_us, statistics.median(samples), samples[int(n * 0.95)], n))

    batch = [texts[i % len(texts)] for i in range(64)]
    started = time.perf_counter()
    fast.model.encode(batch)
    batch_us = (time.perf_counter() - started) * 1e6
    print("embed-only batched 64: %.1f us/item" % (batch_us / 64))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
