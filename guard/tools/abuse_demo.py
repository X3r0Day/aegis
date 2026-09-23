#!/usr/bin/env python3
"""Synthetic API-traffic demo for the abuse detector.

Six client profiles are generated with virtual timestamps and fed through the
aggregator, then Laya decides on each one:

    normal_browse        varied endpoints, human-ish pacing        -> allow
    integration_poll     legit steady poller, one endpoint         -> allow
    scraper              sequential user IDs, 404 mix, 15/s        -> block
    credential_stuffing  300 logins, 85% 401                       -> block
    burst_dos            2500 requests in 10s, high concurrency    -> block
    parameter_fuzzing    random/malformed paths, bad methods       -> block

    .venv/bin/python guard/tools/abuse_demo.py
    .venv/bin/python guard/tools/abuse_demo.py --mode monitor
    .venv/bin/python guard/tools/abuse_demo.py --formats   # pick the best state format
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent          # guard/tools
SERVER = HERE.parent                            # guard
ROOT = SERVER.parent                            # binary/
sys.path.insert(0, str(SERVER))                 # laya_guard
sys.path.insert(0, str(HERE))                   # sibling tools

os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("HF_HOME", str(ROOT / "laya" / "models" / "hf"))

from laya_guard import AbuseConfig, AbuseDetector  # noqa: E402

EXPECTED = {
    "normal_browse": ("allow", "normal_client"),
    "integration_poll": ("allow", "integration_burst"),
    "scraper": ("block", "scraping_enumeration"),
    "credential_stuffing": ("block", "credential_stuffing"),
    "burst_dos": ("block", "rate_abuse_dos"),
    "parameter_fuzzing": ("block", "parameter_fuzzing"),
}


def ev(ts: float, **kwargs: Any) -> Dict[str, Any]:
    base = {"ts": ts, "method": "GET", "status": 200}
    base.update(kwargs)
    return base


# ------------------------------------------------------------------ profiles
def profile_normal(now: float, rng: random.Random) -> List[Dict[str, Any]]:
    events = []
    endpoints = ["/api/v1/orders", "/api/v1/products", "/api/v1/me", "/api/v1/cart", "/api/v1/search"]
    for i in range(24):
        ts = now - 60 + i * 2.5 + rng.uniform(-0.9, 0.9)
        path = rng.choice(endpoints)
        if path == "/api/v1/orders" and rng.random() < 0.5:
            path += "/%d" % rng.randint(1000, 5000)
        events.append(ev(ts, ip="198.51.100.10", path=path, method=rng.choice(["GET", "GET", "GET", "POST"]),
                         status=rng.choice([200, 200, 200, 200, 201, 304, 404]),
                         bytes_out=rng.randint(500, 4000),
                         asn="3320", country="DE", datacenter=False,
                         user_agent="Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"))
    return events


def profile_integration(now: float, rng: random.Random) -> List[Dict[str, Any]]:
    events = []
    for i in range(120):
        ts = now - 60 + i * 0.5
        events.append(ev(ts, ip="198.51.100.20", path="/api/v1/jobs/status", route="/api/v1/jobs/status",
                         api_key_id="key_integration", status=200, in_flight=rng.randint(1, 2),
                         bytes_out=350, asn="64513", country="IE", datacenter=True,
                         client_age_s=2592000, prior_requests_24h=172800, prior_error_rate=0.001,
                         user_agent="acme-integration-bot/2.4 (system-monitor)"))
    return events


def profile_scraper(now: float, rng: random.Random) -> List[Dict[str, Any]]:
    events = []
    for i in range(900):
        ts = now - 60 + i * (60.0 / 900)
        events.append(ev(ts, ip="203.0.113.9", path="/api/v1/users/%d" % (10000 + i),
                         route="/api/v1/users/{id}",
                         status=404 if i % 3 == 0 else 200, in_flight=8,
                         bytes_out=1200, asn="24940", country="DE", datacenter=True,
                         user_agent="python-requests/2.32.3"))
    return events


def profile_credential_stuffing(now: float, rng: random.Random) -> List[Dict[str, Any]]:
    events = []
    for i in range(300):
        ts = now - 60 + i * 0.2
        events.append(ev(ts, ip="203.0.113.44", path="/api/v1/auth/login", route="/api/v1/auth/login",
                         method="POST", status=401 if rng.random() < 0.85 else 200,
                         in_flight=4, bytes_in=180, asn="9009", country="NL", datacenter=True,
                         username="user%04d@corp.example" % i,
                         user_agent="python-requests/2.31.0"))
    return events


def profile_burst_dos(now: float, rng: random.Random) -> List[Dict[str, Any]]:
    events = []
    for i in range(2500):
        ts = now - 10 + i * (10.0 / 2500)
        events.append(ev(ts, ip="203.0.113.77", path="/api/v1/search", route="/api/v1/search",
                         status=rng.choice([200, 200, 429, 503]), in_flight=60,
                         bytes_out=2000, asn="14061", country="US", datacenter=True,
                         user_agent="curl/8.6.0"))
    return events


def profile_parameter_fuzzing(now: float, rng: random.Random) -> List[Dict[str, Any]]:
    events = []
    words = ["api", "v1", "v2", "admin", "debug", "backup", "../..", "%2e%2e", "internal"]
    leaves = ["login", "users", "config", "backup.sql", "wp-admin", "gql", "actuator"]
    for i in range(400):
        ts = now - 60 + i * 0.15
        path = "/" + "/".join(rng.choice(words) for _ in range(rng.randint(1, 3))) + "/" + rng.choice(leaves)
        events.append(ev(ts, ip="203.0.113.100", path=path,
                         method=rng.choice(["GET", "POST", "PUT", "DELETE", "OPTIONS", "TRACE"]),
                         status=rng.choice([400, 404, 404, 500, 403]), in_flight=6,
                         bytes_in=rng.randint(0, 4000), asn="16276", country="FR", datacenter=True,
                         user_agent=rng.choice(["", "sqlmap/1.8", "Nikto/2.5"])))
    return events


PROFILES = {
    "normal_browse": profile_normal,
    "integration_poll": profile_integration,
    "scraper": profile_scraper,
    "credential_stuffing": profile_credential_stuffing,
    "burst_dos": profile_burst_dos,
    "parameter_fuzzing": profile_parameter_fuzzing,
}


def build_events(seed: int = 7):
    rng = random.Random(seed)
    now = time.time()
    all_events: List[Dict[str, Any]] = []
    by_profile: Dict[str, List[Dict[str, Any]]] = {}
    for name, fn in PROFILES.items():
        events = fn(now, rng)
        by_profile[name] = events
        all_events.extend(events)
    return all_events, by_profile, now


# --------------------------------------------------------------------- runner
def run(mode: str, threshold: float) -> int:
    events, by_profile, now = build_events()
    detector = AbuseDetector(AbuseConfig(mode=mode, threshold=threshold))
    print("[abuse] %d events across %d profiles · mode=%s · threshold=%.2f"
          % (len(events), len(by_profile), mode, threshold), flush=True)
    print("[abuse] warming the checkpoint (first call ~20 s on cpu)…", flush=True)
    detector.observe(events)
    detector.warm()

    failures = 0
    for name, expected in EXPECTED.items():
        key = by_profile[name][0].get("api_key_id") or by_profile[name][0]["ip"]
        triggers = detector.trigger_reasons(key, now)
        verdict = detector.evaluate(key, force=True, mode=mode, now=now, triggered_by=triggers)
        assert verdict is not None  # force=True always evaluates
        got = verdict.label
        expected_label, expected_category = expected
        if mode == "monitor" and expected_label == "block":
            expected_label = "would-block"
        ok = got == expected_label and verdict.category == expected_category
        if not ok:
            failures += 1
        print("\n[%s] %s" % (name, "PASS" if ok else "FAIL"), flush=True)
        print("  triggers : %s" % (", ".join(triggers) or "none"), flush=True)
        print("  guard    : %-11s abuse=%.3f severity=%.2f category=%-22s action=%-9s (%.0f ms)"
              % (got, verdict.abuse_score, verdict.severity, verdict.category, verdict.recommended_action, verdict.latency_ms), flush=True)
        print("  expected : %s / %s" % (expected_label, expected_category), flush=True)
        if not ok:
            print("  features : %s" % verdict.features.get("flags"), flush=True)

    print("\n[abuse] %d/%d profiles matched" % (len(EXPECTED) - failures, len(EXPECTED)), flush=True)
    return 0


def run_formats(threshold: float) -> int:
    """Compare state wrappings and checkpoints so the best pair can be pinned."""
    events, by_profile, now = build_events()
    detector = AbuseDetector(AbuseConfig(mode="monitor", threshold=threshold))
    detector.observe(events)
    detector.warm()
    runner = detector._ensure_runner()

    for name, expected in EXPECTED.items():
        key = by_profile[name][0].get("api_key_id") or by_profile[name][0]["ip"]
        features = detector.features(key, now)
        narrative = detector.render_state(features)
        print("\n[%s] expected %s · report %d chars" % (name, expected, len(narrative)), flush=True)
        for fmt_name, wrapper in (("traffic", "traffic"), ("plain", None)):
            state = narrative if wrapper is None else {wrapper: narrative}
            result = runner.predict(state, detector.questions, model=detector.cfg.model)
            answers = result["answers"]
            print("  %-6s is_abuse=%.3f true_positive=%.3f severity=%.2f category=%-22s action=%s"
                  % (fmt_name, answers["is_abuse"]["noul"], answers["true_positive"]["noul"],
                     answers["severity"]["score"], answers["category"]["choice"], answers["action"]["choice"]),
                  flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", default="block", choices=["block", "monitor"])
    ap.add_argument("--threshold", type=float, default=0.45)
    ap.add_argument("--formats", action="store_true", help="compare state wrappings instead of running")
    args = ap.parse_args()
    return run_formats(args.threshold) if args.formats else run(args.mode, args.threshold)


if __name__ == "__main__":
    raise SystemExit(main())
