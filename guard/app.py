#!/usr/bin/env python3
"""Aegis: guard server with a web console.

Machine side: OpenAI-compatible proxy, abuse telemetry ingest, check API.
Human side:  /console, first run goes through /setup, then everything is
configured and watched in the browser.

    export DEEPSEEK_API_KEY=sk-...
    .venv/bin/python guard/app.py            # console at http://127.0.0.1:8978

env:
    GUARD_MODE=block|monitor        guard threshold, decision policy
    GUARD_THRESHOLD=0.8
    GUARD_MODEL=english
    GUARD_FASTPATH=0|1              distilled student pre-filter
    ABUSE_MODE, ABUSE_THRESHOLD, ABUSE_MODEL, ABUSE_COOLDOWN
    ABUSE_ENFORCE, ABUSE_BLOCK_TTL, ABUSE_ENFORCE_INTERVAL
    DEMO_RATE_LIMIT=60              rule limiter on /demo-api, 0 to disable
    UPSTREAM_BASE, UPSTREAM_MODEL, UPSTREAM_KEY_ENV
"""
import asyncio
import copy
import hashlib
import json
import os
import random
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("HF_HOME", str(ROOT / "laya/models/hf"))

from laya_guard import (  # noqa: E402
    AbuseConfig,
    AbuseDetector,
    DecisionLog,
    FastGuard,
    GuardConfig,
    LayaGuard,
    Verdict,
    extract_scan_text,
    model_registry,
    rule_decision,
)
from tools import abuse_demo  # noqa: E402
from console import router as console_router, runtime, store  # noqa: E402
from agent import AgentMonitor  # noqa: E402


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


MODE = _env("GUARD_MODE", "block")
THRESHOLD = float(_env("GUARD_THRESHOLD", "0.8"))
GUARD_MODEL = _env("GUARD_MODEL", "english")
UPSTREAM_BASE = _env("UPSTREAM_BASE", "https://api.deepseek.com").rstrip("/")
UPSTREAM_MODEL = _env("UPSTREAM_MODEL", "deepseek-chat")
UPSTREAM_KEY_ENV = _env("UPSTREAM_KEY_ENV", "DEEPSEEK_API_KEY")
LOG_PATH = Path(_env("GUARD_LOG", str(HERE / "logs/decisions.jsonl")))
EXCERPT_CHARS = int(_env("GUARD_EXCERPT_CHARS", "0"))

ABUSE_MODE = _env("ABUSE_MODE", "monitor")
ABUSE_THRESHOLD = float(_env("ABUSE_THRESHOLD", "0.45"))
ABUSE_COOLDOWN = float(_env("ABUSE_COOLDOWN", "60"))
ABUSE_MODEL = _env("ABUSE_MODEL", "typed-decisions")
ABUSE_LOG = Path(_env("ABUSE_LOG", str(HERE / "logs/abuse.jsonl")))
ABUSE_ENFORCE = _env("ABUSE_ENFORCE", "1").lower() not in ("0", "false", "no")
ABUSE_BLOCK_TTL = float(_env("ABUSE_BLOCK_TTL", "60"))
ABUSE_ENFORCE_INTERVAL = float(_env("ABUSE_ENFORCE_INTERVAL", "5"))
GUARD_FASTPATH = _env("GUARD_FASTPATH", "0").lower() not in ("0", "false", "no")
GUARD_FASTPATH_THRESHOLD = float(_env("GUARD_FASTPATH_THRESHOLD", "0.9"))

CHAT_HTML = HERE / "webui/chat.html"
ABUSE_HTML = HERE / "webui/abuse.html"
STATIC_DIR = HERE / "webui/static"

guard = LayaGuard(GuardConfig(model=GUARD_MODEL, threshold=THRESHOLD,
                              mode=MODE, excerpt_chars=EXCERPT_CHARS))
log = DecisionLog(LOG_PATH)

abuse = AbuseDetector(AbuseConfig(model=ABUSE_MODEL, threshold=ABUSE_THRESHOLD,
                                  mode=ABUSE_MODE, cooldown_s=ABUSE_COOLDOWN))
abuse_log = DecisionLog(ABUSE_LOG)

# console settings (SQLite) override the env defaults above
runtime.bind(guard, abuse)
runtime.load()
runtime.agent = AgentMonitor(guard, abuse, runtime, store)

_fast_guard = None
_fast_guard_lock = threading.Lock()


def get_fast_guard():
    global _fast_guard
    if not GUARD_FASTPATH or not FastGuard.available():
        return None
    with _fast_guard_lock:
        if _fast_guard is None:
            _fast_guard = FastGuard(threshold=GUARD_FASTPATH_THRESHOLD)
            print("[guard] fast path loaded, threshold %.2f" % GUARD_FASTPATH_THRESHOLD, flush=True)
    return _fast_guard


def upstream_key():
    return runtime.upstream_key


def _compact_verdict(verdict):
    # small enough to ride in a response header
    compact = {
        "label": verdict.label,
        "decision": verdict.decision,
        "mode": verdict.mode,
        "threshold": verdict.threshold,
        "model": verdict.model,
        "latency_ms": verdict.latency_ms,
        "probabilities": verdict.probabilities,
        "triggers": verdict.triggers,
    }
    return json.dumps(compact, separators=(",", ":"), ensure_ascii=True)


def _warm_all():
    guard.warm()
    abuse.warm()
    try:
        get_fast_guard()
    except Exception as exc:
        print("[guard] fast path unavailable: %r" % exc, flush=True)


def _apply_device_rules():
    # persistent blacklist from the console: keep these clients blocked
    now = time.time()
    for rule in store.list_rules():
        expires = rule.get("expires_at") or 0
        if expires and expires <= now:
            continue
        ttl = max(60, int(expires - now)) if expires else 30 * 86400
        abuse.block_client(rule["device"], ttl, category="device_rule", score=1.0)


async def _enforcement_loop():
    # every few seconds: evaluate triggered clients and block the abusive ones
    # for ABUSE_BLOCK_TTL. the detector itself only observes
    while True:
        await asyncio.sleep(ABUSE_ENFORCE_INTERVAL)
        try:
            _apply_device_rules()
        except Exception as exc:
            print("[guard] device rule sync error: %r" % exc, flush=True)
        if not runtime.abuse_enforce:
            continue
        try:
            verdicts = await run_in_threadpool(abuse.evaluate_triggered, "block")
        except Exception as exc:
            print("[guard] enforcement loop error: %r" % exc, flush=True)
            continue
        for verdict in verdicts:
            abuse_log.append(verdict, meta={"endpoint": "demo-api/enforce", "client": verdict.client})
            store.log_abuse(verdict.to_dict(), {"endpoint": "demo-api/enforce"})
            if verdict.enforced:
                abuse.block_client(verdict.client, runtime.abuse_block_ttl,
                                   category=verdict.category, score=verdict.abuse_score)
                store.log_block(verdict.client, "block", verdict.category, verdict.abuse_score,
                                until=time.time() + runtime.abuse_block_ttl)
                print("[guard] blocked %s for %.0fs, %s (%.3f)"
                      % (verdict.client, runtime.abuse_block_ttl, verdict.category, verdict.abuse_score), flush=True)


async def _agent_loop():
    # 24/7 monitor: snapshot the guard every agent_interval seconds and act on
    # anything that slipped through the automatic enforcement
    while True:
        await asyncio.sleep(max(5, runtime.agent_interval))
        if not runtime.agent_enabled or runtime.agent is None:
            continue
        try:
            await run_in_threadpool(runtime.agent.run_once)
        except Exception as exc:
            print("[guard] agent monitor error: %r" % exc, flush=True)


async def _retention_loop():
    # keep the console database inside the configured retention window
    while True:
        await asyncio.sleep(3600)
        try:
            store.purge(runtime.retention_days)
        except Exception as exc:
            print("[guard] retention purge failed: %r" % exc, flush=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store.purge(runtime.retention_days)
    _apply_device_rules()
    threading.Thread(target=_warm_all, daemon=True).start()
    task = asyncio.create_task(_enforcement_loop())
    retention = asyncio.create_task(_retention_loop())
    agent_task = asyncio.create_task(_agent_loop())
    try:
        yield
    finally:
        task.cancel()
        retention.cancel()
        agent_task.cancel()
        retention.cancel()


app = FastAPI(title="Aegis", version="0.2.0", lifespan=lifespan)
app.include_router(console_router)


@app.get("/")
def index():
    return RedirectResponse(url="/console", status_code=307)


@app.get("/chat")
def chat_page():
    return FileResponse(CHAT_HTML)


@app.get("/api-abuse")
def abuse_page():
    return FileResponse(ABUSE_HTML)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _cuda():
    try:
        import torch

        return torch.cuda.is_available()
    except Exception:
        return False


@app.get("/health")
def health():
    return {
        "status": "ok",
        "key_env": UPSTREAM_KEY_ENV,
        "key_set": bool(runtime.upstream_key),
        "key_source": runtime.key_source(),
        "upstream": runtime.upstream_base,
        "upstream_model": runtime.upstream_model,
        "mode": runtime.guard_mode,
        "threshold": guard.cfg.threshold,
        "model": GUARD_MODEL,
        "loaded": guard.loaded_models,
        "device": "cuda" if _cuda() else "cpu",
        "abuse": {
            "mode": runtime.abuse_mode,
            "threshold": runtime.abuse_threshold,
            "model": ABUSE_MODEL,
            "cooldown_s": ABUSE_COOLDOWN,
            "enforce": runtime.abuse_enforce,
            "block_ttl_s": runtime.abuse_block_ttl,
            "enforce_interval_s": ABUSE_ENFORCE_INTERVAL,
        },
        "blocked_clients": abuse.blocked_clients(),
        "agent": {
            "enabled": runtime.agent_enabled,
            "mode": runtime.agent_mode,
            "interval_s": runtime.agent_interval,
            "llm": runtime.agent_llm,
            "runs": runtime.agent.runs if runtime.agent else 0,
            "actions": runtime.agent.actions_taken if runtime.agent else 0,
            "last_run_age_s": (round(time.time() - runtime.agent.last_run, 1)
                               if runtime.agent and runtime.agent.last_run else None),
            "last_error": runtime.agent.last_error if runtime.agent else "",
        },
        "console": {
            "configured": store.admin_configured(),
            "retention_days": runtime.retention_days,
            "db": str(store.path),
        },
        "fastpath": {
            "enabled": GUARD_FASTPATH,
            "artifact": FastGuard.available(),
            "loaded": _fast_guard is not None,
            "threshold": GUARD_FASTPATH_THRESHOLD,
            "metrics": (_fast_guard.metrics if _fast_guard is not None else None),
        },
        "demo_api": {"rate_limit_per_s": _DEMO_RATE_LIMIT},
    }


@app.get("/api/models")
def api_models():
    return {
        "models": [entry.to_dict() for entry in model_registry()],
        "guard_default": GUARD_MODEL,
        "abuse_default": ABUSE_MODEL,
    }


@app.get("/api/decisions")
def decisions(limit: int = 60):
    return {"decisions": log.tail(limit)}


def _request_mode(request):
    header = (request.headers.get("x-guard-mode") or "").lower()
    return header if header in ("block", "monitor") else guard.cfg.mode


@app.post("/v1/guard/check")
async def guard_check(request: Request):
    try:
        payload = await request.json()
    except Exception:
        body = (await request.body()).decode("utf-8", "replace")
        payload = {"input": body}
    mode = _request_mode(request)
    verdict = await run_in_threadpool(guard.check_request, payload, mode=mode)
    log.append(verdict, meta={"endpoint": "guard/check"})
    store.log_guard(verdict.to_dict(), {"endpoint": "guard/check"})
    return verdict.to_dict()


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": {"message": "body must be json"}})

    messages = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(messages, list) or not messages:
        return JSONResponse(
            status_code=422,
            content={"error": {"message": "messages must be a non-empty list", "type": "invalid_request_error"}},
        )

    mode = _request_mode(request)

    # cheap student first, but only for confident blocks, and never against a
    # rule-allow (greetings for example)
    fast = get_fast_guard()
    if fast is not None and mode == "block":
        text, scope = extract_scan_text(messages, guard.cfg.scope)
        fast_result = fast.score(text)
        rule = rule_decision(text) or ""
        if fast_result.probability >= GUARD_FASTPATH_THRESHOLD and not rule.startswith("allow"):
            fast_verdict = Verdict(
                decision="block", blocked=True, enforced=True, mode=mode,
                threshold=GUARD_FASTPATH_THRESHOLD,
                thresholds={"fastpath": GUARD_FASTPATH_THRESHOLD},
                probabilities={"fastpath_block": fast_result.probability},
                triggers=[{"question": "fastpath_block", "probability": fast_result.probability,
                           "threshold": GUARD_FASTPATH_THRESHOLD}],
                confidences={},
                model=fast_result.model,
                routing={"model": fast_result.model, "reason": "distilled student (sub-ms fast path)",
                         "repo": None, "detection": None, "workflow": None},
                latency_ms=round(fast_result.latency_us / 1000.0, 3),
                text_sha256=hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest(),
                scope=scope,
            )
            log.append(fast_verdict, meta={"endpoint": "chat/completions", "fastpath": True,
                                           "model": fast_result.model})
            store.log_guard(fast_verdict.to_dict(), {"endpoint": "chat/completions", "fastpath": True})
            return JSONResponse(
                status_code=403,
                content={
                    "error": {"message": "blocked by Aegis Guard (fast path): prompt injection",
                              "type": "prompt_injection_blocked", "code": "guard_blocked"},
                    "guard": fast_verdict.to_dict(),
                },
                headers={"X-Guard-Verdict": _compact_verdict(fast_verdict), "X-Guard-Fastpath": "1"},
            )

    verdict = await run_in_threadpool(guard.check_messages, messages, mode=mode)
    log.append(verdict, meta={"endpoint": "chat/completions", "model": payload.get("model") or runtime.upstream_model})
    store.log_guard(verdict.to_dict(), {"endpoint": "chat/completions"})

    if verdict.enforced:
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "message": "blocked by Aegis Guard: prompt injection",
                    "type": "prompt_injection_blocked",
                    "code": "guard_blocked",
                },
                "guard": verdict.to_dict(),
            },
            headers={"X-Guard-Verdict": _compact_verdict(verdict)},
        )

    key = upstream_key()
    if not key:
        return JSONResponse(
            status_code=502,
            content={
                "error": {"message": "%s not set on the guard server" % UPSTREAM_KEY_ENV,
                          "type": "upstream_key_missing"},
                "guard": verdict.to_dict(),
            },
            headers={"X-Guard-Verdict": _compact_verdict(verdict)},
        )

    forward = dict(payload)
    forward["model"] = forward.get("model") or runtime.upstream_model
    forward.pop("guard_mode", None)

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
            upstream = await client.post(
                runtime.upstream_base + "/chat/completions",
                json=forward,
                headers={"Authorization": "Bearer %s" % key},
            )
    except httpx.HTTPError as exc:
        return JSONResponse(
            status_code=502,
            content={"error": {"message": "upstream failed: %s" % exc, "type": "upstream_error"},
                     "guard": verdict.to_dict()},
            headers={"X-Guard-Verdict": _compact_verdict(verdict)},
        )

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
        headers={
            "X-Guard-Verdict": _compact_verdict(verdict),
            "X-Upstream-Ms": "%.0f" % ((time.perf_counter() - started) * 1000.0),
        },
    )


def _abuse_mode(request):
    header = (request.headers.get("x-abuse-mode") or "").lower()
    return header if header in ("block", "monitor") else abuse.cfg.mode


def _events_from_body(body):
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("events", "requests", "logs"):
            if isinstance(body.get(key), list):
                return body[key]
    raise ValueError("body must be a list of events, or an object with an events list")


@app.post("/v1/abuse/events")
async def abuse_events(request: Request):
    try:
        payload = await request.json()
        events = _events_from_body(payload)
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"error": {"message": str(exc), "type": "invalid_request_error"}})

    accepted = len(events)
    try:
        await run_in_threadpool(abuse.observe, events)
    except (ValueError, TypeError) as exc:
        return JSONResponse(status_code=422, content={"error": {"message": "bad event: %s" % exc, "type": "invalid_request_error"}})

    mode = _abuse_mode(request)
    verdicts = await run_in_threadpool(abuse.evaluate_triggered, mode)
    for verdict in verdicts:
        abuse_log.append(verdict, meta={"endpoint": "abuse/events", "client": verdict.client})
        store.log_abuse(verdict.to_dict(), {"endpoint": "abuse/events"})
        if verdict.enforced:
            abuse.block_client(verdict.client, runtime.abuse_block_ttl,
                               category=verdict.category, score=verdict.abuse_score)
            store.log_block(verdict.client, "block", verdict.category, verdict.abuse_score,
                            until=time.time() + runtime.abuse_block_ttl)
    return {"accepted": accepted, "evaluations": [v.to_dict() for v in verdicts]}


@app.get("/v1/abuse/clients")
def abuse_clients(top: int = 20):
    return {"clients": abuse.clients(top)}


@app.post("/v1/abuse/check")
async def abuse_check(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": {"message": "body must be json"}})

    mode = _abuse_mode(request)
    if isinstance(payload, dict) and payload.get("client"):
        key = str(payload["client"])
        try:
            verdict = await run_in_threadpool(abuse.evaluate, key, force=True, mode=mode)
        except KeyError as exc:
            return JSONResponse(status_code=404, content={"error": {"message": str(exc)}})
        if verdict is None:
            return JSONResponse(status_code=404, content={"error": {"message": "no traffic for client %r" % key}})
        abuse_log.append(verdict, meta={"endpoint": "abuse/check", "client": key})
        store.log_abuse(verdict.to_dict(), {"endpoint": "abuse/check"})
        return {"verdicts": [verdict.to_dict()]}

    try:
        events = _events_from_body(payload)
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"error": {"message": str(exc), "type": "invalid_request_error"}})
    verdicts = await run_in_threadpool(abuse.check_events, events, mode)
    for verdict in verdicts:
        abuse_log.append(verdict, meta={"endpoint": "abuse/check", "client": verdict.client})
        store.log_abuse(verdict.to_dict(), {"endpoint": "abuse/check"})
    return {"verdicts": [v.to_dict() for v in verdicts]}


@app.get("/api/abuse/decisions")
def abuse_decisions(limit: int = 60):
    return {"decisions": abuse_log.tail(limit)}


# the bench hits this with real requests. it plays the customer API behind the
# guard: every request becomes telemetry, blocked clients get 429 until TTL ends
_DEMO_RATE = {}
_DEMO_INFLIGHT = {}
_DEMO_RATE_LIMIT = float(_env("DEMO_RATE_LIMIT", "60"))  # 0 = off, Laya only


def _demo_client(request):
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else "?"


def _demo_rate_limited(client):
    if _DEMO_RATE_LIMIT <= 0:
        return False
    now = time.time()
    bucket = _DEMO_RATE.get(client)
    if bucket is None or now - bucket[0] >= 1.0:
        _DEMO_RATE[client] = [now, 1]
        return False
    bucket[1] += 1
    return bucket[1] > _DEMO_RATE_LIMIT


def _demo_decr_inflight(client):
    _DEMO_INFLIGHT[client] = max(0, _DEMO_INFLIGHT.get(client, 1) - 1)


@app.middleware("http")
async def demo_api_middleware(request: Request, call_next):
    path = request.url.path
    if path != "/demo-api" and not path.startswith("/demo-api/"):
        return await call_next(request)

    client = _demo_client(request)
    started = time.time()
    user_agent = request.headers.get("user-agent")
    inflight = _DEMO_INFLIGHT.get(client, 0) + 1
    _DEMO_INFLIGHT[client] = inflight

    def record(status, size):
        try:
            abuse.observe([{
                "ts": started,
                "method": request.method,
                "path": path,
                "status": status,
                "latency_ms": round((time.time() - started) * 1000, 2),
                "bytes_out": size,
                "ip": client,
                "user_agent": user_agent,
                "in_flight": inflight,
            }])
        except Exception:
            pass

    if runtime.abuse_enforce:
        block = abuse.block_state(client)
        if block:
            record(429, 0)
            _demo_decr_inflight(client)
            return JSONResponse(status_code=429, content={
                "detail": "blocked by Aegis Guard: %s (retry in %ss)"
                          % (block.get("category") or "abuse", int(block["remaining_s"])),
                "guard": {"client": client, **block},
            })

    if _demo_rate_limited(client):
        record(429, 0)
        _demo_decr_inflight(client)
        return JSONResponse(status_code=429, content={"detail": "rate limit exceeded (demo API rule)"})

    try:
        response = await call_next(request)
    except Exception:
        record(500, 0)
        _demo_decr_inflight(client)
        raise
    record(response.status_code, int(response.headers.get("content-length") or 0))
    _demo_decr_inflight(client)
    return response


@app.get("/demo-api")
@app.get("/demo-api/")
def demo_index():
    return {
        "service": "demo API, the stand-in for the customer API behind Aegis Guard",
        "endpoints": [
            "GET  /demo-api/v1/products",
            "GET  /demo-api/v1/products/{id}   (1-500)",
            "GET  /demo-api/v1/orders",
            "GET  /demo-api/v1/orders/{id}     (1-2000)",
            "GET  /demo-api/v1/me",
            "GET  /demo-api/v1/cart",
            "GET  /demo-api/v1/search?q=",
            "GET  /demo-api/v1/jobs/status",
            "GET  /demo-api/v1/users/{id}      (1-4900)",
            "POST /demo-api/v1/auth/login      (username ending 00 + password 'correct')",
        ],
        "notes": "every request is recorded as telemetry; Aegis Guard blocks abusive clients with 429",
    }


@app.get("/demo-api/v1/products")
def demo_products(request: Request):
    return {"items": [{"id": i, "name": "Product %d" % i} for i in range(1, 6)]}


@app.get("/demo-api/v1/products/{product_id}")
def demo_product(product_id: int, request: Request):
    if 1 <= product_id <= 500:
        return {"id": product_id, "name": "Product %d" % product_id, "stock": 12}
    raise HTTPException(status_code=404, detail="product not found")


@app.get("/demo-api/v1/orders")
def demo_orders(request: Request):
    return {"items": [{"id": 1042, "status": "open"}]}


@app.get("/demo-api/v1/orders/{order_id}")
def demo_order(order_id: int, request: Request):
    if 1 <= order_id <= 2000:
        return {"id": order_id, "status": "open", "total": 42.5}
    raise HTTPException(status_code=404, detail="order not found")


@app.get("/demo-api/v1/me")
def demo_me(request: Request):
    return {"user": "demo", "plan": "pro"}


@app.get("/demo-api/v1/cart")
def demo_cart(request: Request):
    return {"items": [], "total": 0.0}


@app.get("/demo-api/v1/search")
def demo_search(request: Request, q: str = ""):
    return {"query": q, "results": [{"id": 1, "name": "Result for %s" % (q or "demo")}]}


@app.get("/demo-api/v1/jobs/status")
def demo_job_status(request: Request):
    return {"status": "ok", "queue_depth": 3, "last_run": "2026-09-23T18:00:00"}


@app.get("/demo-api/v1/users/{user_id}")
def demo_user(user_id: int, request: Request):
    if 1 <= user_id <= 4900:
        return {"id": user_id, "name": "User %d" % user_id, "email": "user%d@corp.example" % user_id}
    raise HTTPException(status_code=404, detail="user not found")


@app.post("/demo-api/v1/auth/login")
async def demo_login(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    username = str(body.get("username") or "")
    if username.endswith("00") and str(body.get("password") or "") == "correct":
        return {"token": "demo-token", "user": username}
    raise HTTPException(status_code=401, detail="invalid credentials")


def _base_plan(**overrides):
    plan = {
        "target": "",              # request.base_url + /demo-api, filled in below
        "method": "GET",
        "pick": "cycle",
        "paths": ["/v1/me"],
        "count": 20,
        "rate_per_s": 5,
        "concurrency": 4,
        "client_ip": "127.0.0.1",
        "user_agent": "demo-client/1.0",
        "headers": {},
    }
    plan.update(overrides)
    return plan


LIVE_PLANS = {
    "normal_browse": _base_plan(
        client_ip="198.51.100.10",
        user_agent="Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
        asn="3320", country="DE", datacenter=False,
        paths=["/v1/products", "/v1/search?q=shoes", "/v1/me", "/v1/cart", "/v1/orders/{id}"],
        count=18, rate_per_s=2.5, jitter=0.7, id_start=1995, concurrency=2,
    ),
    "integration_poll": _base_plan(
        client_ip="198.51.100.20",
        user_agent="acme-integration-bot/2.4 (system-monitor)",
        asn="64513", country="IE", datacenter=True,
        api_key_id="key_integration",
        client_age_s=2592000, prior_requests_24h=172800, prior_error_rate=0.001,
        paths=["/v1/jobs/status"], count=60, rate_per_s=10, concurrency=2,
    ),
    "scraper": _base_plan(
        client_ip="203.0.113.9",
        user_agent="python-requests/2.32.3",
        asn="24940", country="DE", datacenter=True,
        # long enough for the enforcement loop to cut it off mid-run
        paths=["/v1/users/{id}"], count=300, rate_per_s=15, id_start=4800, id_step=1, concurrency=8,
    ),
    "credential_stuffing": _base_plan(
        client_ip="203.0.113.44",
        user_agent="python-requests/2.31.0",
        asn="9009", country="NL", datacenter=True,
        method="POST", paths=["/v1/auth/login"], auth=True, count=120, rate_per_s=20, concurrency=4,
    ),
    "burst_dos": _base_plan(
        client_ip="203.0.113.77",
        user_agent="curl/8.6.0",
        asn="14061", country="US", datacenter=True,
        paths=["/v1/search?q=load"], count=250, rate_per_s=100, concurrency=30,
    ),
    "parameter_fuzzing": _base_plan(
        client_ip="203.0.113.100",
        user_agent="sqlmap/1.8",
        asn="16276", country="FR", datacenter=True,
        pick="random",
        paths=["/v1/admin/config", "/v1/backup.sql", "/v1/debug", "/v1/internal/users",
               "/wp-admin", "/v1/users/999999", "/v1/gql", "/v1/actuator/env", "/v1/../../etc/passwd"],
        count=80, rate_per_s=15, concurrency=6,
    ),
}


def _plan_for(profile_id, request):
    plan = copy.deepcopy(LIVE_PLANS[profile_id])
    if not plan.get("target"):
        plan["target"] = str(request.base_url).rstrip("/") + "/demo-api"
    return plan


async def _execute_plan(plan):
    # real requests, real statuses, paced by rate_per_s with optional jitter
    target = str(plan.get("target") or "").rstrip("/")
    if not target.startswith(("http://", "https://")):
        raise ValueError("target must be an http(s) url")
    count = max(1, min(int(plan.get("count") or 1), 2000))
    rate = max(0.1, min(float(plan.get("rate_per_s") or 1), 500.0))
    concurrency = max(1, min(int(plan.get("concurrency") or 8), 64))
    jitter = max(0.0, min(float(plan.get("jitter") or 0.0), 1.0))
    interval = 1.0 / rate
    method = str(plan.get("method") or "GET").upper()
    pick = str(plan.get("pick") or "cycle")
    paths = plan.get("paths")
    if not isinstance(paths, list) or not paths:
        raise ValueError("paths must be a non-empty list")
    id_start = int(plan.get("id_start") or 1)
    id_step = int(plan.get("id_step") or 1)
    auth = bool(plan.get("auth"))
    client_ip = str(plan.get("client_ip") or "127.0.0.1")
    user_agent = str(plan.get("user_agent") or "demo-client/1.0")
    extra_headers = {str(k): str(v) for k, v in (plan.get("headers") or {}).items()}
    context = {key: plan[key] for key in
               ("asn", "country", "datacenter", "api_key_id", "client_age_s",
                "prior_requests_24h", "prior_error_rate") if key in plan}

    headers = {"User-Agent": user_agent, "X-Forwarded-For": client_ip, **extra_headers}
    rng = random.Random(7)
    semaphore = asyncio.Semaphore(concurrency)
    events = []
    statuses = {}
    errors = 0

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        async def fire(index, path):
            nonlocal errors
            async with semaphore:
                started = time.time()
                body = {"username": "user%04d" % index, "password": "guess-%d" % index} if auth else None
                status, size, guard_blocked = 0, 0, False
                try:
                    response = await client.request(method, target + path, headers=headers, json=body)
                    status = response.status_code
                    size = len(response.content)
                    if status == 429 and "Aegis Guard" in response.text[:400]:
                        guard_blocked = True
                except httpx.HTTPError:
                    errors += 1
                if status:
                    key = "429_laya" if guard_blocked else str(status)
                    statuses[key] = statuses.get(key, 0) + 1
                event = {
                    "ts": started,
                    "method": method,
                    "path": path,
                    "status": status,
                    "latency_ms": round((time.time() - started) * 1000, 2),
                    "bytes_out": size,
                    "ip": client_ip,
                    "user_agent": user_agent,
                    **context,
                }
                if auth:
                    event["username"] = "user%04d" % index
                events.append(event)

        start = time.time()
        tasks = []
        for i in range(count):
            due = start + i * interval
            if jitter:
                due += rng.uniform(-jitter, jitter) * interval
            delay = due - time.time()
            if delay > 0:
                await asyncio.sleep(delay)
            template = str(paths[i % len(paths)] if pick != "random" else rng.choice(paths))
            path = template.replace("{id}", str(id_start + i * id_step))
            tasks.append(asyncio.create_task(fire(i, path)))
        if tasks:
            await asyncio.gather(*tasks)
        duration = time.time() - start

    return {"events": events, "duration_s": round(duration, 2), "statuses": statuses, "errors": errors}


ABUSE_PROFILES = [
    {
        "id": "normal_browse",
        "name": "Normal user browsing",
        "expect": "allow",
        "description": "Varied endpoints at a human pace, browser user agent, residential IP.",
    },
    {
        "id": "integration_poll",
        "name": "Legit integration poller",
        "expect": "allow",
        "description": "A known keyed client polls one endpoint steadily, automated but legitimate.",
    },
    {
        "id": "scraper",
        "name": "Scraper / enumeration",
        "expect": "block",
        "description": "Sequential user IDs walked at speed, a third returning 404.",
    },
    {
        "id": "credential_stuffing",
        "name": "Credential stuffing",
        "expect": "block",
        "description": "Login attempts at speed, almost all rejected, a different username each time.",
    },
    {
        "id": "burst_dos",
        "name": "Burst / rate abuse",
        "expect": "block",
        "description": "A flood of requests in seconds, far above the API rate limit.",
    },
    {
        "id": "parameter_fuzzing",
        "name": "Parameter fuzzer",
        "expect": "block",
        "description": "Random and malformed paths, mixed methods, sqlmap user agent.",
    },
]


@app.get("/api-abuse/profiles")
def api_abuse_profiles():
    return {"profiles": ABUSE_PROFILES, "default_mode": "block", "model": ABUSE_MODEL}


def _capture_sample(events, head=50, tail=70):
    # start of the run plus how it ended, so a mid-run block is visible
    if len(events) <= head + tail:
        return events
    return events[:head] + events[-tail:]


def _event_stats(events):
    if not events:
        return {"count": 0}
    clients, endpoints, stamps = {}, {}, []
    for event in events:
        key = event.get("api_key_id") or event.get("ip") or "?"
        clients[key] = clients.get(key, 0) + 1
        endpoint = event.get("route") or event.get("path") or "?"
        endpoints[endpoint] = endpoints.get(endpoint, 0) + 1
        try:
            stamps.append(float(event.get("ts", 0)))
        except (TypeError, ValueError):
            pass
    top_endpoint = max(endpoints.items(), key=lambda kv: kv[1]) if endpoints else ("?", 0)
    span = (max(stamps) - min(stamps)) if stamps else 0.0
    return {
        "count": len(events),
        "clients": sorted(clients)[:3],
        "span_s": round(span, 1),
        "top_endpoint": top_endpoint[0],
        "top_endpoint_share": round(top_endpoint[1] / len(events), 3),
    }


def _generate_profile(profile_id, seed):
    return abuse_demo.PROFILES[profile_id](time.time(), random.Random(seed))


@app.post("/api-abuse/preview")
async def api_abuse_preview(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": {"message": "body must be json"}})
    profile_id = body.get("profile")
    if profile_id not in abuse_demo.PROFILES:
        return JSONResponse(status_code=422, content={"error": {"message": "unknown profile %r" % profile_id}})
    seed = int(body.get("seed") or 7)
    events = _generate_profile(profile_id, seed)
    return {
        "profile": profile_id,
        "seed": seed,
        "events": events,
        "stats": _event_stats(events),
        "plan": _plan_for(profile_id, request),
    }


@app.post("/api-abuse/simulate")
async def api_abuse_simulate(request: Request):
    # either a profile name or a raw events list from the editor
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": {"message": "body must be json"}})

    mode = str(body.get("mode") or "block").lower()
    if mode not in ("block", "monitor"):
        mode = "block"

    if isinstance(body.get("events"), list):
        events = body["events"]
        if not events:
            return JSONResponse(status_code=422, content={"error": {"message": "no events"}})
        if len(events) > 50000:
            return JSONResponse(status_code=422, content={"error": {"message": "too many events (max 50000)"}})
        if not all(isinstance(event, dict) for event in events):
            return JSONResponse(status_code=422, content={"error": {"message": "every event must be an object"}})
        profile_id = str(body.get("profile") or "custom")
    else:
        profile_id = body.get("profile")
        if profile_id not in abuse_demo.PROFILES:
            return JSONResponse(status_code=422, content={"error": {"message": "unknown profile %r" % profile_id}})
        events = _generate_profile(profile_id, int(body.get("seed") or 7))

    started = time.perf_counter()
    try:
        verdicts = await run_in_threadpool(abuse.check_events, events, mode)
    except (ValueError, TypeError) as exc:
        return JSONResponse(status_code=422, content={"error": {"message": "bad event payload: %s" % exc}})
    elapsed = (time.perf_counter() - started) * 1000.0
    for verdict in verdicts:
        abuse_log.append(verdict, meta={"endpoint": "api-abuse/simulate", "profile": profile_id})
        store.log_abuse(verdict.to_dict(), {"endpoint": "api-abuse/simulate"})

    return {
        "profile": profile_id,
        "mode": mode,
        "events": len(events),
        "elapsed_ms": round(elapsed, 1),
        "verdict": verdicts[0].to_dict() if verdicts else None,
    }


@app.post("/api-abuse/live")
async def api_abuse_live(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": {"message": "body must be json"}})

    profile_id = str(body.get("profile") or "")
    plan = body.get("plan")
    if plan is None:
        if profile_id not in LIVE_PLANS:
            return JSONResponse(status_code=422, content={"error": {"message": "unknown profile %r" % profile_id}})
        plan = _plan_for(profile_id, request)
    elif not isinstance(plan, dict):
        return JSONResponse(status_code=422, content={"error": {"message": "plan must be an object"}})
    if not profile_id:
        profile_id = "custom"

    mode = str(body.get("mode") or "block").lower()
    if mode not in ("block", "monitor"):
        mode = "block"

    client = str(plan.get("client_ip") or "").strip()
    if client:
        abuse.reset_client(client)   # repeat runs start from a clean slate

    try:
        result = await _execute_plan(plan)
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"error": {"message": str(exc)}})
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": {"message": "execution failed: %s" % exc}})

    verdicts = await run_in_threadpool(abuse.check_events, result["events"], mode)
    for verdict in verdicts:
        abuse_log.append(verdict, meta={"endpoint": "api-abuse/live", "profile": profile_id})
        store.log_abuse(verdict.to_dict(), {"endpoint": "api-abuse/live"})

    return {
        "profile": profile_id,
        "mode": mode,
        "events": len(result["events"]),
        "duration_s": result["duration_s"],
        "statuses": result["statuses"],
        "errors": result["errors"],
        "plan": plan,
        "captured": _capture_sample(result["events"]),
        "verdict": verdicts[0].to_dict() if verdicts else None,
    }


@app.post("/api-abuse/unblock")
async def api_abuse_unblock(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": {"message": "body must be json"}})
    client = body.get("client")
    if not client:
        return JSONResponse(status_code=422, content={"error": {"message": "client is required"}})
    cleared = abuse.clear_block(str(client))
    if cleared:
        store.log_block(str(client), "unblock")
    return {"client": str(client), "unblocked": cleared}


def main():
    import argparse

    ap = argparse.ArgumentParser(description="Aegis Guard server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8978)
    args = ap.parse_args()

    print("[aegis] console  http://%s:%d/console" % (args.host, args.port), flush=True)
    print("[aegis] upstream %s (%s), key %s"
          % (runtime.upstream_base, runtime.upstream_model, runtime.key_source()), flush=True)
    print("[aegis] guard %s | abuse %s%s"
          % (runtime.guard_mode, runtime.abuse_mode, " +enforce" if runtime.abuse_enforce else ""), flush=True)
    print("[aegis] db %s" % store.path, flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
