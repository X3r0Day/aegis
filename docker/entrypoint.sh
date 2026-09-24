#!/usr/bin/env bash
# Aegis container entrypoint: reuse the Laya checkpoints on the host when
# they exist, download them only when they are missing, then start the guard
# server on 0.0.0.0. The model directory lives on a volume, so restarts and
# rebuilds never download twice.
set -e

MODELS="${AEGIS_MODELS_DIR:-/app/laya/models/laya}"

if [ -f "$MODELS/model.safetensors" ] && [ -f "$MODELS/typed-decisions/model.safetensors" ]; then
    echo "[aegis] Laya checkpoints found in $MODELS, skipping download"
else
    echo "[aegis] Laya checkpoints missing in $MODELS"
    echo "[aegis] downloading english + typed-decisions (~1.7 GB, one time)"
    python laya/download_models.py english typed-decisions
fi

echo "[aegis] starting guard on 0.0.0.0:${PORT:-8978}"
exec python guard/app.py --host 0.0.0.0 --port "${PORT:-8978}"
