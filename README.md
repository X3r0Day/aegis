# PS - 8 Insider Threat, Anomalous Access & Autonomous Cyber Defense

A unified AI-powered cybersecurity platform that combines insider threat detection with autonomous cyber defense. The system continuously monitors internal logs, user behavior, and API activity using graph-based anomaly detection to flag suspicious access patterns and insider threats. An AI agent layer actively detects and responds to external threats — prompt injection attacks, malicious agent behavior, abnormal API calls — triggering automated countermeasures in real time. It watches from the inside and defends from the outside: a 360° intelligent security system.

Core modules: Log Analytics & Graph Anomaly Engine · AI Agent Threat Monitor · Real-time Alert & Response Dashboard · Prompt Injection & API Abuse Detector

## Layout

Two parts, one shared venv:

```
binary/
├── .venv/            # shared Python 3.12 env (torch CPU, laya, fastapi, model2vec, …)
├── laya/             # PART 1 — Laya model, demos, raw playground   (laya/README.md)
│   ├── models/       #    checkpoints (english · multilingual · typed-decisions)
│   ├── webapp/       #    playground UI at :8765 (testing raw Laya, not the product)
│   └── download_models.py · demo.py · scenarios.py
├── guard/            # PART 2 — the guard web server at :8978      (guard/README.md)
│   ├── app.py        #    FastAPI: chat firewall + abuse telemetry + demo API
│   ├── console.py    #    web console: auth, settings, monitoring APIs
│   ├── store.py      #    SQLite (decisions, blocks, settings, retention)
│   ├── laya_guard/   #    the framework (guard, abuse, rules, fast path, registry)
│   ├── webui/        #    /console, /chat and /api-abuse pages
│   ├── tools/        #    demos, evals, student training (abuse_demo, guard_eval, fastpath_*)
│   ├── models/       #    fastguard.joblib (distilled sub-ms student)
│   ├── data/         #    aegis.db (SQLite, gitignored)
│   └── logs/         #    runtime decision logs (JSONL, gitignored)
└── demo-scripts/     # standalone stress client used against the demo API
```

## Quick start

```bash
.venv/bin/python guard/app.py            # guard server: http://127.0.0.1:8978/console
.venv/bin/python laya/webapp/app.py      # Laya playground: http://127.0.0.1:8765
```

First run opens the console setup page — pick an admin password (≥ 8 chars).
From there the dashboard tracks LLM calls guarded, injection blocks and API
abuse verdicts, and every setting lives in SQLite instead of only env vars.

The guard owns the upstream key (`DEEPSEEK_API_KEY` in the environment) and drops
prompt-injection requests and abusive clients before they reach the model; the Laya
playground is only for experimenting with the decision model itself.

See `guard/README.md` for endpoints, the abuse detector, the sub-millisecond fast
path and Windows setup; `laya/README.md` for the model setup and demo commands.

## Docker

```bash
DEEPSEEK_API_KEY=sk-... docker compose up --build
```

Then open http://127.0.0.1:8978/console. The container:

- reuses `laya/models/laya` from the host if the checkpoints are already there
  (mount, no download), and downloads english + typed-decisions (~1.7 GB) only
  when they are missing, into the same folder so it happens once;
- stores models and HF cache on volumes (`AEGIS_MODELS`, `AEGIS_HF` env
  vars to point at checkpoints stored elsewhere);
- keeps state (console settings, blacklist, agent audit) in `guard/data/`;
- ships a healthcheck and runs the guard on port 8978.

Image is about 2 GB (CPU torch). No Docker? Run it the plain way instead:
`.venv/bin/python guard/app.py`.
