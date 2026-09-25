# Laya — System 1 decision engine (local setup)

[Laya](https://huggingface.co/convaiinnovations/laya) is a **non-autoregressive System 1
decision model**: give it a **state** (text / JSON / conversation) plus typed **questions**
(`choice` / `score` / `noul`*) and it returns typed answers with calibrated probabilities in
a single forward pass, across 100+ languages. It never generates text, so there is nothing
to parse and nothing to hallucinate. Apache 2.0, open weights.

This folder is the Laya part of the repo: local checkpoints, demo scripts and the raw
model playground. The shared Python 3.12 venv lives at the repo root (`../.venv`);
runtime artifacts stay under `laya/models/`.

\* `noul` = "no / yes, unless ..." — the package's boolean question type; returns P(true).

## Layout

```
laya/
├── models/
│   └── laya/            # checkpoints, HF-style layout (see ./download_models.py)
│       ├── model.safetensors           # english   (ModernBERT-large, 421M)
│       ├── rl_agent_config.json
│       ├── encoder/ tokenizer/
│       └── multilingual/               # multilingual (mmBERT-base, 322M)
├── download_models.py   # fetch/refresh checkpoints into ./models (idempotent)
├── demo.py              # offline CLI demo: guardrails / alert triage / email phishing
├── scenarios.py         # scenario data shared by demo.py and the web playground
├── webapp/
│   ├── app.py           # FastAPI playground server (interactive UI)
│   ├── static/          # index.html · style.css · app.js
│   └── playground.png   # screenshot
├── requirements.txt
└── README.md            # this file
```

The shared venv (`../.venv`) and the guard web server (`../guard/`) live at the repo root.

## Setup (reproduce from scratch)

```bash
# from the repo root (the venv is shared with the guard server)
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python --torch-backend=cpu torch
uv pip install --python .venv/bin/python laya fastapi uvicorn httpx model2vec scikit-learn
cd laya
../.venv/bin/python download_models.py              # english + multilingual (~1.5 GB)
# or: ../.venv/bin/python download_models.py --all  # + typed-decisions (~0.85 GB)
```

Notes:
- `--torch-backend=cpu` keeps the install CPU-only (this machine has no usable GPU).
  Equivalent pip form: `pip install torch --index-url https://download.pytorch.org/whl/cpu`.
- Checkpoints land in `models/laya/` exactly mirroring the HF repo, so a local path can be
  passed to `laya.load()` — fully offline once downloaded.

## Run the demo

```bash
../.venv/bin/python demo.py                  # all scenarios, english checkpoint
../.venv/bin/python demo.py guard            # prompt-injection guardrails only
../.venv/bin/python demo.py triage --json    # security alert triage, raw JSON
../.venv/bin/python demo.py all --model auto # route english/multilingual by detected script
```

## Web playground

An interactive UI for testing custom states, scenarios and questions:

```bash
../.venv/bin/python webapp/app.py            # -> http://127.0.0.1:8765
# ../.venv/bin/python webapp/app.py --port 9000
```

![Laya Playground](webapp/playground.png)

- **State panel** — edit the state as JSON (or plain text) with scenario presets:
  prompt-injection guardrails, insider-threat alert (Binary Hacks PS-8), phishing email,
  support-ticket triage, content moderation, LLM routing, blank.
- **Questions panel** — visual builder for `choice` / `score` / `noul` questions:
  add/remove questions, edit option keys/descriptions and ordered score levels; an
  “edit as JSON” mode is available for full control (e.g. custom `true`/`false`
  wording on `noul`).
- **Results** — per-question verdict, full probability bars, confidence and
  escalate chips, routed model + reason, latency, token count, raw JSON view.

Notes: the first request that touches a checkpoint builds it (~20 s on CPU); warm
calls are ~0.6–1.5 s (english) / ~0.2–0.6 s (multilingual, `--model auto` picks by
script). The server binds `127.0.0.1` only — pass `--host 0.0.0.0` to expose it.

## Quickstart (library)

```python
import os
os.environ["USE_TF"] = "0"          # avoid the TF/abseil import deadlock (model-card gotcha)

import laya

agent = laya.load("models/laya")                                   # english (local path)
# agent = laya.load("models/laya", subfolder="multilingual")       # 100+ languages

questions = {
    "severity": {
        "type": "score",
        "instructions": "How severe is this security alert?",
        "criteria": ["benign", "low", "suspicious", "critical"],
    },
    "requires_escalation": {
        "type": "noul",
        "instructions": "Should this be escalated to the security team immediately?",
    },
}

result = agent.predict({"event": "bulk_file_download", "user": "j.doe", "volume_mb": 842}, questions)
result["answers"]["severity"]["score"]                    # e.g. 2.9 (ordinal expectation)
result["answers"]["requires_escalation"]["noul"]          # e.g. 0.97 -> P(escalate)
```

Every question in one call is answered in **one forward pass** — latency scales with
`state length + option budget`, not with the number of questions. Build the question schema at
request time; new question types need no retraining (options are scored at their own `[MASK]`
tokens).

### Routing between checkpoints

```python
from laya import Router

router = Router(models={                                     # point the router at local weights
    "english": ("models/laya", None),
    "multilingual": ("models/laya", "multilingual"),
})
res = router.predict({"body": "मुझसे दो बार शुल्क लिया गया, कृपया पैसे वापस करें।"}, questions)
res["routing"]["model"]     # -> "multilingual" (non-Latin script detected in <1 ms)
```

### Useful presets shipped with the package

| Preset | Use |
|---|---|
| `laya.guard_questions()` | jailbreak / prompt-injection / sensitive-data guardrails |
| `laya.email_questions()` | email triage + phishing/spam flags (`laya.email_state()` builds the state) |
| `laya.moderation_questions()` | toxicity / harassment / threats |
| `laya.triage_questions()` | customer-support ticket triage |
| `laya.router_questions()` | model-routing / difficulty estimation |

## Checkpoints

| Name | Backbone | Params | Context | Best at |
|---|---|---|---|---|
| `english` | ModernBERT-large | 421M | 512 | English decisions (repo root) |
| `multilingual` | mmBERT-base | 322M | 1024 | 100+ languages, ~2.2× faster (subfolder) |
| `typed-decisions` | ModernBERT-large | 421M | 1024 | 4 fine-tuned workflows (optional, 0.766 acc) |

The English checkpoint **collapses on non-Latin scripts while staying confident** — for
anything outside English (e.g. Hindi logs) use `multilingual` or `Router`, which detects the
script before the forward pass.

## Notes for the Binary Hacks PS-8 platform (insider threat / autonomous defense)

- **Prompt Injection & API Abuse Detector** → `guard_questions()` + `Router`: a single pass
  over input text yields P(jailbreak), P(prompt_injection), P(sensitive_data) + harm severity
  — usable as a fast pre-filter in front of the main LLM.
- **Log Analytics & Graph Anomaly Engine** → `score` + `noul` questions over structured event
  JSON (user, action, volume, time, MFA counts) gives per-alert severity, category, and an
  escalation probability for the dashboard.
- **Real-time** — one forward pass per event; measure on this machine with the demo.
  For serving, keep the agent resident (`Router(preload=True)` or a module-level `laya.load`)
  — a cold load costs seconds, inference costs hundreds of ms on CPU.
- **Calibration caveat**: the base checkpoints ship over-confident; refit one temperature per
  (question type, option count) on your own data (package exposes `clamp_temperature`,
  `ece_score`) before trusting absolute probabilities for auto-actions.

## Machine notes

- `USE_TF=0` is set by `demo.py` / `webapp/app.py` and should be set in your app before
  importing `laya` (transformers probes TensorFlow at import; the TF abseil runtime can
  deadlock model build — the model card documents this).
- Installed here: Python 3.12.12, torch 2.14.0+cpu, transformers 5.17.0, laya 0.3.6,
  fastapi 0.141.1, uvicorn 0.53.0.
- No GPU on this machine → CPU inference, `torch.float32`.
- Measured on this machine (CPU): first checkpoint build ≈ 20 s; warm single-question
  call ≈ 0.3 s (english) / ≈ 0.24 s (multilingual); a 5-question batch ≈ 1.4–2 s;
  the web playground’s English/injection run (5 questions) took ≈ 1.4 s warm.
