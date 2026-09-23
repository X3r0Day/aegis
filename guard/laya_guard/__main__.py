"""CLI: check a string or a request file against the guard.

    python -m laya_guard "ignore all previous instructions"
    python -m laya_guard --messages '[{"role":"user","content":"..."}]'
    python -m laya_guard --request-file request.json
    echo "text to check" | python -m laya_guard
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import GuardConfig
from .guard import LayaGuard


def _load_payload(args) -> dict:
    if args.request_file:
        payload = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            payload = {"messages": payload}
        return payload
    if args.messages:
        return {"messages": json.loads(args.messages)}
    if args.text is not None:
        return {"input": args.text}
    if not sys.stdin.isatty():
        raw = sys.stdin.read()
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"input": raw}
        except ValueError:
            return {"input": raw}
    raise SystemExit("nothing to check — pass text, --messages, --request-file, or stdin")


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="laya_guard",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("text", nargs="?", help="text to check")
    ap.add_argument("--messages", help="OpenAI-style messages as a JSON string")
    ap.add_argument("--request-file", help="JSON file: request body, messages list, or plain text")
    ap.add_argument("--model", default="auto", choices=["auto", "english", "multilingual"])
    ap.add_argument("--threshold", type=float, default=0.8)
    ap.add_argument("--mode", default="block", choices=["block", "monitor"])
    ap.add_argument("--scope", default="latest_user", choices=["latest_user", "all_user", "all"])
    ap.add_argument("--device", default=None)
    ap.add_argument("--quiet", action="store_true", help="print only the label")
    args = ap.parse_args()

    config = GuardConfig(
        model=args.model,
        threshold=args.threshold,
        mode=args.mode,
        scope=args.scope,
        device=args.device,
    )
    payload = _load_payload(args)
    verdict = LayaGuard(config).check_request(payload)

    if args.quiet:
        print(verdict.label)
        return 0 if not verdict.enforced else 1
    print(json.dumps(verdict.to_dict(), indent=2, ensure_ascii=False, default=str))
    return 0 if not verdict.enforced else 1


if __name__ == "__main__":
    raise SystemExit(main())
