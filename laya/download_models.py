#!/usr/bin/env python3
"""Fetch Laya checkpoints into ./models/laya.

The HF repo (convaiinnovations/laya) bundles the family:

    english         421M  ModernBERT-large, 512 ctx      repo root
    multilingual    322M  mmBERT-base, 100+ langs        multilingual/
    typed-decisions 421M  decision fine-tune             typed-decisions/

Only the requested patterns get pulled, default is english + multilingual
(~1.5 GB total):

    ./download_models.py                 # english + multilingual
    ./download_models.py english
    ./download_models.py --all           # adds typed-decisions, another ~0.85 GB
"""
import argparse
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "models/laya"
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("checkpoints", nargs="*", choices=[*CHECKPOINTS],
                    help="what to download (default: english multilingual)")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    names = list(CHECKPOINTS) if args.all else (args.checkpoints or ["english", "multilingual"])
    patterns = [p for name in names for p in CHECKPOINTS[name]]

    # keep HF metadata under ./models instead of ~/.cache
    os.environ.setdefault("HF_HOME", str(HERE / "models/hf"))

    from huggingface_hub import snapshot_download

    print("[laya] downloading %s" % ", ".join(names))
    print("[laya] -> %s" % TARGET)
    path = snapshot_download(REPO_ID, allow_patterns=patterns, local_dir=str(TARGET))
    print("[laya] done: %s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
