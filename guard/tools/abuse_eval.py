#!/usr/bin/env python3
"""Abuse bench: profiles rendered as prose, several checkpoints and question
schemas side by side. Settled on typed-decisions and the merged schema with
this; rerun it when trying something new.

    .venv/bin/python guard/tools/abuse_eval.py
    .venv/bin/python guard/tools/abuse_eval.py --models english typed-decisions
"""
import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # guard/tools
SERVER = HERE.parent                            # guard
ROOT = SERVER.parent                            # binary/
sys.path.insert(0, str(SERVER))                 # laya_guard
sys.path.insert(0, str(HERE))                   # sibling tools

os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("HF_HOME", str(ROOT / "laya" / "models" / "hf"))

from laya_guard import AbuseConfig, AbuseDetector  # noqa: E402
from laya_guard.abuse import abuse_questions  # noqa: E402
import abuse_demo  # noqa: E402


INCIDENT_QUESTIONS = {
    "credential_compromise": {
        "type": "noul",
        "instructions": "In `traffic`, do the requests indicate credential compromise or credential abuse (stolen or brute-forced credentials being used)?",
    },
    "true_positive": {
        "type": "noul",
        "instructions": "Is the client in `traffic` a true security concern (attacking, extracting data, or abusing the API) rather than benign activity?",
    },
    "severity": {
        "type": "score",
        "instructions": "How severe is the security risk from this client in `traffic`?",
        "criteria": ["informational: normal or clearly legitimate traffic", "low: noisy but harmless",
                     "high: attacking or extracting data", "critical: active attack causing or risking outage"],
    },
    "urgency": {
        "type": "score",
        "instructions": "How urgently should a security team act on this client in `traffic`?",
        "criteria": ["no action needed", "monitor when convenient", "act today", "immediate response required"],
    },
    "disposition": {
        "type": "choice",
        "instructions": "What should be done about the client in `traffic`?",
        "criteria": {"allow": "no action", "throttle": "rate-limit", "block": "block the client",
                     "escalate": "escalate to a human analyst"},
    },
}

ABUSE_SCHEMA = {
    "category": abuse_questions()["category"],
    "is_abuse": abuse_questions()["is_abuse"],
    "severity": abuse_questions()["severity"],
    "action": abuse_questions()["action"],
}

MERGED_SCHEMA = {
    "category": abuse_questions()["category"],
    "is_abuse": abuse_questions()["is_abuse"],
    "true_positive": INCIDENT_QUESTIONS["true_positive"],
    "severity": abuse_questions()["severity"],
    "action": abuse_questions()["action"],
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", default=["english", "typed-decisions"])
    args = ap.parse_args()

    events, by_profile, now = abuse_demo.build_events()
    detector = AbuseDetector(AbuseConfig(model="english"))
    detector.observe(events)
    detector.warm()
    runner = detector._ensure_runner()

    for model in args.models:
        if model != "english" and not (ROOT / "laya" / "models" / "laya" / model / "model.safetensors").exists():
            print("checkpoint %s not present; skipping" % model)
            continue
        if model != "english":
            runner.load(model)
        for schema_name, schema in (("abuse-schema", ABUSE_SCHEMA),
                                    ("incident-schema", INCIDENT_QUESTIONS),
                                    ("merged-schema", MERGED_SCHEMA)):
            print("\n" + "=" * 100)
            print("MODEL %s · SCHEMA %s" % (model, schema_name))
            for name, expected in abuse_demo.EXPECTED.items():
                key = by_profile[name][0].get("api_key_id") or by_profile[name][0]["ip"]
                f = detector.features(key, now)
                narrative = detector.render_state(f)
                result = runner.predict({"traffic": narrative}, schema, model=model)
                answers = result["answers"]
                bits = []
                if "category" in answers:
                    bits.append("cat=%s" % answers["category"]["choice"])
                if "is_abuse" in answers:
                    bits.append("abuse=%.3f" % answers["is_abuse"]["noul"])
                if "true_positive" in answers:
                    bits.append("TP=%.3f" % answers["true_positive"]["noul"])
                if "is_abuse" in answers and "true_positive" in answers:
                    bits.append("AB=%.3f" % ((answers["is_abuse"]["noul"] + answers["true_positive"]["noul"]) / 2))
                if "credential_compromise" in answers:
                    bits.append("cred=%.3f" % answers["credential_compromise"]["noul"])
                if "severity" in answers:
                    bits.append("sev=%.2f" % answers["severity"]["score"])
                if "urgency" in answers:
                    bits.append("urg=%.2f" % answers["urgency"]["score"])
                if "disposition" in answers:
                    bits.append("disp=%s" % answers["disposition"]["choice"])
                if "action" in answers:
                    bits.append("act=%s" % answers["action"]["choice"])
                print("  %-22s expected %-30s %s" % (name, str(expected), " ".join(bits)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
