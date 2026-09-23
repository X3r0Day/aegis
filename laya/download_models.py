#!/usr/bin/env python3
"""Download Laya checkpoints into ./models/laya (kept inside this folder).

The Hugging Face repo (convaiinnovations/laya) bundles the whole family:

    english         421M  ModernBERT-large, 512 tok context   -> repo root
    multilingual    322M  mmBERT-base, 1024 tok, 100+ langs   -> multilingual/
    typed-decisions 421M  ModernBERT-large, decision workflows -> typed-decisions/

Only the files matching --checkpoints are fetched, so the default download is
~1.5 GB total (english + multilingual):
    ./download_models.py                     # english + multilingual
    ./download_models.py english             # english only
    ./download_models.py --all               # every checkpoint
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "models" / "laya"
REPO_ID = "convaiinnovations/laya"

CHECKPOINTS = {
    "english": [
        "rl_agent_config.json",
        "model.safetensors",
        "tokenizer/*",
        "encoder/config.json",
    ],
    "multilingual": ["multilingual/*"],
    "typed-decisions": ["typed-decisions/*"],
}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "checkpoints",
        nargs="*",
        choices=[*CHECKPOINTS],
        help="checkpoints to download (default: english multilingual)",
    )
    ap.add_argument("--all", action="store_true", help="download every checkpoint")
    args = ap.parse_args()

    names = list(CHECKPOINTS) if args.all else (args.checkpoints or ["english", "multilingual"])
    patterns = [p for name in names for p in CHECKPOINTS[name]]

    # Everything lands under ./models: no writes to ~/.cache/huggingface.
    os.environ.setdefault("HF_HOME", str(HERE / "models" / "hf"))

    from huggingface_hub import snapshot_download

    print("[laya] downloading %s" % ", ".join(names))
    print("[laya] -> %s" % TARGET)
    path = snapshot_download(
        REPO_ID,
        allow_patterns=patterns,
        local_dir=str(TARGET),
    )
    print("[laya] done: %s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
