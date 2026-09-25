#!/usr/bin/env python3
"""Playground server for experimenting with the local Laya model.

    ../.venv/bin/python webapp/app.py            # http://127.0.0.1:8765
    ../.venv/bin/python webapp/app.py --port 9000

checkpoints come from ../models/laya, nothing here talks to the network.
inference is serialized because it all runs on the CPU.
"""
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# same env tricks as the CLI: no TF probing, HF metadata stays local
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("HF_HOME", str(ROOT / "models/hf"))
sys.path.insert(0, str(ROOT))       # for `import scenarios`

import torch  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import laya  # noqa: E402
from laya import (  # noqa: E402
    Router,
    email_questions,
    guard_questions,
    moderation_questions,
    router_questions,
    triage_questions,
)
import scenarios  # noqa: E402

MODEL_DIR = ROOT / "models/laya"
STATIC_DIR = HERE / "static"

QTYPES = ("choice", "score", "noul")
MODELS = ("auto", "english", "multilingual")

_router = None
_router_lock = threading.Lock()
_predict_lock = threading.Lock()


def get_router():
    global _router
    with _router_lock:
        if _router is None:
            _router = Router(
                models={
                    "english": (str(MODEL_DIR), None),
                    "multilingual": (str(MODEL_DIR), "multilingual"),
                },
                max_loaded=2,
                default="english",
            )
    return _router


def _checkpoint_present(name):
    if name == "english":
        return (MODEL_DIR / "model.safetensors").is_file()
    if name == "multilingual":
        return (MODEL_DIR / "multilingual/model.safetensors").is_file()
    return (MODEL_DIR / name / "model.safetensors").is_file()


def _preload():
    # build the english checkpoint in the background so the first click is fast
    try:
        agent = get_router().load("english")
        agent.predict({"warmup": True},
                      {"ready": {"type": "noul", "instructions": "Is the model warm?"}})
        print("[webapp] english loaded and warmed", flush=True)
    except Exception as exc:
        print("[webapp] preload failed: %r" % exc, flush=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    threading.Thread(target=_preload, daemon=True).start()
    yield


app = FastAPI(title="Laya Playground", version="1.0", lifespan=lifespan)


def _validate_questions(questions):
    if not isinstance(questions, dict) or not questions:
        raise HTTPException(422, "questions must be a non-empty JSON object")
    if len(questions) > 32:
        raise HTTPException(422, "too many questions (max 32)")
    for qid, q in questions.items():
        if not isinstance(qid, str) or not qid.strip():
            raise HTTPException(422, "question ids must be non-empty strings")
        if not isinstance(q, dict):
            raise HTTPException(422, "question %r must be an object" % qid)
        qtype = q.get("type")
        if qtype not in QTYPES:
            raise HTTPException(422, "question %r: type must be one of %s" % (qid, list(QTYPES)))
        if not isinstance(q.get("instructions"), str) or not q["instructions"].strip():
            raise HTTPException(422, "question %r: instructions are required" % qid)
        criteria = q.get("criteria")
        if qtype == "choice":
            if not isinstance(criteria, dict) or len(criteria) < 2:
                raise HTTPException(422, "question %r: choice needs at least 2 options" % qid)
        elif qtype == "score":
            if not isinstance(criteria, list) or len(criteria) < 2:
                raise HTTPException(422, "question %r: score needs at least 2 levels" % qid)


def _validate_state(state):
    if isinstance(state, str):
        if len(state) > 250_000:
            raise HTTPException(422, "state is too large (max 250k chars)")
        return
    if isinstance(state, (dict, list)):
        return
    raise HTTPException(422, "state must be a string, object or array")


class PredictIn(BaseModel):
    state: object
    questions: object
    model: str = "auto"


class PredictOut(BaseModel):
    model: str
    answers: object
    usage: object
    routing: object = None
    latency_ms: float


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
def status():
    loaded = _router.loaded if _router is not None else []
    return {
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        "checkpoints": {
            name: {"present": _checkpoint_present(name), "loaded": name in loaded}
            for name in ("english", "multilingual")
        },
        "loaded": loaded,
    }


@app.get("/api/presets")
def presets():
    return {
        "models": [
            {"id": "auto", "name": "Auto (route by language)"},
            {"id": "english", "name": "English, ModernBERT-large (421M)"},
            {"id": "multilingual", "name": "Multilingual, mmBERT-base (322M)"},
        ],
        "scenarios": [
            {
                "id": "prompt_guard",
                "name": "Prompt-injection guardrails",
                "description": "An LLM input that tries to override the assistant's rules.",
                "state": scenarios.GUARD_STATE,
                "questions": guard_questions(),
            },
            {
                "id": "insider_alert",
                "name": "Insider-threat alert (Binary Hacks PS-8)",
                "description": "SIEM alert: bulk payroll download to personal mail after resignation.",
                "state": scenarios.TRIAGE_ALERT,
                "questions": scenarios.TRIAGE_QUESTIONS,
            },
            {
                "id": "phishing_email",
                "name": "Phishing email",
                "description": "Account-suspension phishing with a credential-harvesting link.",
                "state": scenarios.EMAIL,
                "questions": email_questions(),
            },
            {
                "id": "ticket_triage",
                "name": "Support ticket triage",
                "description": "Angry customer, duplicate charge, churn threat.",
                "state": scenarios.TICKET,
                "questions": triage_questions(),
            },
            {
                "id": "moderation",
                "name": "Content moderation",
                "description": "Toxic post with a threat against a person.",
                "state": scenarios.POST,
                "questions": moderation_questions(),
            },
            {
                "id": "llm_router",
                "name": "LLM request routing",
                "description": "Estimate difficulty and domain to route a request to the right model.",
                "state": scenarios.MODEL_REQUEST,
                "questions": router_questions(),
            },
            {
                "id": "blank",
                "name": "Blank / custom",
                "description": "Start from scratch and write your own state and questions.",
                "state": scenarios.BLANK_STATE,
                "questions": {
                    "is_relevant": {
                        "type": "noul",
                        "instructions": "Is this text relevant to security?",
                    },
                    "category": {
                        "type": "choice",
                        "instructions": "What is this text about?",
                        "criteria": {"security": "security, threats, incidents", "other": "anything else"},
                    },
                },
            },
        ],
    }


@app.post("/api/predict", response_model=PredictOut)
def predict(req: PredictIn):
    _validate_state(req.state)
    _validate_questions(req.questions)
    if req.model not in MODELS:
        raise HTTPException(422, "model must be one of %s" % list(MODELS))

    router = get_router()
    t0 = time.perf_counter()
    with _predict_lock:
        result = router.predict(req.state, req.questions,
                                model=None if req.model == "auto" else req.model)
    latency = (time.perf_counter() - t0) * 1000.0

    return PredictOut(
        model=result.get("model", "laya-rl-agent"),
        answers=result["answers"],
        usage=result.get("usage"),
        routing=result.get("routing"),
        latency_ms=round(latency, 1),
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main():
    import argparse

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
