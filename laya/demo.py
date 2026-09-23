#!/usr/bin/env python3
"""Offline demo against the local checkpoints, no network at inference time.

    ../.venv/bin/python demo.py                 # all scenarios
    ../.venv/bin/python demo.py guard           # guardrails only
    ../.venv/bin/python demo.py triage --json
    ../.venv/bin/python demo.py all --model auto
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# transformers probes for TensorFlow at import, and with TF around its abseil
# runtime can deadlock the model build (the model card warns about this)
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("HF_HOME", str(HERE / "models/hf"))

import laya  # noqa: E402
from laya import Router, email_questions, guard_questions  # noqa: E402

from scenarios import EMAIL, GUARD_PROMPTS, TRIAGE_ALERT, TRIAGE_QUESTIONS  # noqa: E402

MODEL_DIR = HERE / "models/laya"
MULTILINGUAL_DIR = MODEL_DIR / "multilingual"


def get_agent(model, device):
    if model == "english":
        return laya.load(str(MODEL_DIR), device=device), "english"
    if model == "multilingual":
        if not MULTILINGUAL_DIR.is_dir():
            sys.exit("[laya] multilingual checkpoint missing, run ./download_models.py multilingual")
        return laya.load(str(MODEL_DIR), subfolder="multilingual", device=device), "multilingual"
    raise ValueError(model)


def get_router(device):
    if not MULTILINGUAL_DIR.is_dir():
        sys.exit("[laya] --model auto needs both checkpoints, run ./download_models.py")
    return Router(
        models={
            "english": (str(MODEL_DIR), None),
            "multilingual": (str(MODEL_DIR), "multilingual"),
        },
        device=device,
        default="english",
    )


def run(agent, state, questions):
    t0 = time.perf_counter()
    result = agent.predict(state, questions)
    return result, (time.perf_counter() - t0) * 1000.0


def fmt_probs(probs):
    return "  ".join("%s=%.3f" % (k, v) for k, v in probs.items())


def fmt_answer(qid, ans):
    kind = ans["type"]
    if kind == "choice":
        head = "%-18s choice -> %-20s %s" % (qid, ans["choice"], fmt_probs(ans["probabilities"]))
    elif kind == "score":
        top = len(ans["legend"]) - 1
        head = "%-18s score  -> %.2f/%-4d %s" % (qid, ans["score"], top, fmt_probs(ans["probabilities"]))
    else:
        head = "%-18s noul   -> %.3f true" % (qid, ans["noul"])
    return "%s   (conf %.2f, act %.2f)" % (head, ans["confidence"], ans["action"]["act_probability"])


def show(title, rows, elapsed_ms, routing, as_json):
    if as_json:
        print(json.dumps({
            "title": title,
            "latency_ms": round(elapsed_ms, 1),
            "routing": routing,
            "states": [{"input": label, "answers": res["answers"]} for label, res in rows],
        }, ensure_ascii=False, indent=2))
        return
    print("\n" + "=" * 78)
    print("## %s" % title)
    if routing:
        print("   routed to : %s (%s)" % (routing["model"], routing["reason"]))
    print("   latency   : %.0f ms for %d entr%s" % (elapsed_ms, len(rows), "y" if len(rows) == 1 else "ies"))
    for label, res in rows:
        print("\n   -- %s" % label)
        for qid, ans in res["answers"].items():
            print("      " + fmt_answer(qid, ans))


def demo_guard(agent, as_json):
    questions = guard_questions()
    rows = []
    t0 = time.perf_counter()
    for label, prompt in GUARD_PROMPTS:
        res, _ = run(agent, {"prompt": prompt}, questions)
        rows.append((label, res))
    elapsed = (time.perf_counter() - t0) * 1000.0
    show("prompt-injection guardrails (laya.guard_questions)", rows, elapsed, None, as_json)


def demo_triage(agent, as_json):
    res, elapsed = run(agent, TRIAGE_ALERT, TRIAGE_QUESTIONS)
    show("security alert triage (insider-threat)", [("bulk_file_download @ 02:13", res)], elapsed, None, as_json)


def demo_email(agent, as_json):
    res, elapsed = run(agent, EMAIL, email_questions())
    show("phishing email triage (laya.email_questions)", [("suspension email", res)], elapsed, None, as_json)


SCENARIOS = {"guard": demo_guard, "triage": demo_triage, "email": demo_email}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenarios", nargs="*", choices=[*SCENARIOS, "all"], default=None,
                    help="which scenario(s) to run (default: all)")
    ap.add_argument("--model", default="english", choices=["english", "multilingual", "auto"])
    ap.add_argument("--device", default=None, help="cpu / cuda / mps, default auto")
    ap.add_argument("--json", action="store_true", help="raw json output")
    args = ap.parse_args()

    names = args.scenarios or ["all"]
    if "all" in names:
        names = list(SCENARIOS)

    if args.model == "auto":
        agent = get_router(args.device)
        print("[laya] router ready, english + multilingual")
    else:
        agent, _ = get_agent(args.model, args.device)
        print("[laya] loaded %s from %s (%s)" % (args.model, MODEL_DIR, agent.device))

    for name in names:
        SCENARIOS[name](agent, args.json)

    if not args.json:
        print("\n" + "=" * 78)
        print("done | model: %s | checkpoints: %s" % (args.model, MODEL_DIR))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
