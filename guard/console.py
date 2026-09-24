"""Console backend: first run setup, login sessions, runtime settings, dashboard API.

Settings live in the SQLite store and are applied to the live guard and abuse
detector objects, so most changes take effect without a restart. The console is
the only thing behind auth. The machine endpoints (/v1/...) stay open and are
meant to sit behind the customer's own network or gateway.
"""
import hashlib
import hmac
import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from store import store

HERE = Path(__file__).resolve().parent
WEBUI = HERE / "webui"

COOKIE = "aegis"
SESSION_TTL = 7 * 86400

STARTED_AT = time.time()

SETTING_KEYS = (
    "guard_mode",
    "guard_threshold_prompt_injection",
    "guard_threshold_hidden_instructions",
    "guard_threshold_secret_request",
    "abuse_mode",
    "abuse_threshold",
    "abuse_block_ttl",
    "abuse_enforce",
    "retention_days",
    "upstream_base",
    "upstream_model",
    "upstream_key",
    "agent_enabled",
    "agent_mode",
    "agent_interval",
    "agent_llm",
)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _upstream_key_env() -> str:
    return _env("UPSTREAM_KEY_ENV", "DEEPSEEK_API_KEY")


class Runtime:
    """Current settings, applied to the live guard objects."""

    def __init__(self):
        self.guard: Any = None
        self.abuse: Any = None
        self.guard_mode = "block"
        self.guard_thresholds = {}
        self.abuse_mode = "monitor"
        self.abuse_threshold = 0.45
        self.abuse_enforce = True
        self.abuse_block_ttl = 60.0
        self.retention_days = 7
        self.upstream_base = _env("UPSTREAM_BASE", "https://api.deepseek.com")
        self.upstream_model = _env("UPSTREAM_MODEL", "deepseek-chat")
        self.upstream_key = ""
        self.agent_enabled = True
        self.agent_mode = "act"
        self.agent_interval = 20
        self.agent_llm = True
        self.agent: Any = None

    def bind(self, guard, abuse):
        self.guard = guard
        self.abuse = abuse

    def load(self):
        env_base = _env("UPSTREAM_BASE", "https://api.deepseek.com").rstrip("/")
        env_model = _env("UPSTREAM_MODEL", "deepseek-chat")
        env_key = os.environ.get(_upstream_key_env(), "")

        self.guard_mode = store.get("guard_mode")
        self.guard_thresholds = {
            "prompt_injection": float(store.get("guard_threshold_prompt_injection")),
            "hidden_instructions": float(store.get("guard_threshold_hidden_instructions")),
            "secret_request": float(store.get("guard_threshold_secret_request")),
        }
        self.abuse_mode = store.get("abuse_mode")
        self.abuse_threshold = float(store.get("abuse_threshold"))
        self.abuse_block_ttl = float(store.get("abuse_block_ttl"))
        self.abuse_enforce = store.get("abuse_enforce") in ("1", "true", "yes", "on")
        self.retention_days = int(store.get("retention_days"))
        self.upstream_base = (store.get("upstream_base") or env_base).rstrip("/")
        self.upstream_model = store.get("upstream_model") or env_model
        self.upstream_key = store.get("upstream_key") or env_key
        self.agent_enabled = store.get("agent_enabled") in ("1", "true", "yes", "on")
        self.agent_mode = store.get("agent_mode")
        self.agent_interval = int(store.get("agent_interval"))
        self.agent_llm = store.get("agent_llm") in ("1", "true", "yes", "on")
        self.apply()

    def apply(self):
        if self.guard is not None:
            self.guard.cfg.mode = self.guard_mode
            self.guard.cfg.thresholds = dict(self.guard_thresholds)
            self.guard.cfg.threshold = self.guard_thresholds.get("prompt_injection", 0.8)
        if self.abuse is not None:
            self.abuse.cfg.mode = self.abuse_mode
            self.abuse.cfg.threshold = self.abuse_threshold

    def key_source(self):
        if store.get("upstream_key"):
            return "console"
        if os.environ.get(_upstream_key_env()):
            return "environment"
        return "missing"


runtime = Runtime()


def _session_ok(request):
    raw = request.cookies.get(COOKIE)
    if not raw:
        return False
    secret = store.get("session_secret", "")
    if not secret:
        return False
    exp, _, sig = raw.partition(".")
    try:
        exp_i = int(exp)
    except ValueError:
        return False
    if exp_i < time.time():
        return False
    want = hmac.new(secret.encode(), exp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, sig)


def _set_session(response):
    exp = str(int(time.time()) + SESSION_TTL)
    secret = store.get("session_secret")
    sig = hmac.new(secret.encode(), exp.encode(), hashlib.sha256).hexdigest()
    response.set_cookie(COOKIE, "%s.%s" % (exp, sig), max_age=SESSION_TTL,
                        httponly=True, samesite="lax", path="/")
    return response


def require_session(request: Request):
    if not store.admin_configured():
        raise HTTPException(409, "setup required")
    if not _session_ok(request):
        raise HTTPException(401, "login required")


router = APIRouter()

NO_STORE = {"Cache-Control": "no-store"}


@router.get("/setup")
def setup_page(request: Request):
    if store.admin_configured() and _session_ok(request):
        return RedirectResponse("/console", status_code=307)
    if store.admin_configured():
        return RedirectResponse("/login", status_code=307)
    return FileResponse(WEBUI / "setup.html", headers=NO_STORE)


@router.get("/login")
def login_page(request: Request):
    if not store.admin_configured():
        return RedirectResponse("/setup", status_code=307)
    if _session_ok(request):
        return RedirectResponse("/console", status_code=307)
    return FileResponse(WEBUI / "login.html", headers=NO_STORE)


@router.get("/console")
def console_page(request: Request):
    if not store.admin_configured():
        return RedirectResponse("/setup", status_code=307)
    if not _session_ok(request):
        return RedirectResponse("/login", status_code=307)
    return FileResponse(WEBUI / "console.html", headers=NO_STORE)


@router.post("/console/api/setup")
async def api_setup(request: Request):
    if store.admin_configured():
        raise HTTPException(409, "already configured")
    body = await request.json()
    password = str(body.get("password") or "")
    if len(password) < 8:
        raise HTTPException(422, "password must be at least 8 characters")
    store.set_admin_password(password)
    response = JSONResponse({"ok": True})
    return _set_session(response)


@router.post("/console/api/login")
async def api_login(request: Request):
    body = await request.json()
    if not store.check_admin_password(str(body.get("password") or "")):
        raise HTTPException(401, "wrong password")
    return _set_session(JSONResponse({"ok": True}))


@router.post("/console/api/logout")
def api_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, path="/")
    return response


@router.post("/console/api/password")
async def api_password(request: Request):
    require_session(request)
    body = await request.json()
    if not store.check_admin_password(str(body.get("current") or "")):
        raise HTTPException(401, "current password is wrong")
    new = str(body.get("new") or "")
    if len(new) < 8:
        raise HTTPException(422, "new password must be at least 8 characters")
    store.set_admin_password(new)
    return {"ok": True}


def _masked_key():
    stored = store.get("upstream_key")
    if stored:
        tail = stored[-4:] if len(stored) >= 4 else "****"
        return {"set": True, "source": "console", "hint": "****" + tail}
    if os.environ.get(_upstream_key_env()):
        return {"set": True, "source": "environment", "hint": "from " + _upstream_key_env()}
    return {"set": False, "source": "missing", "hint": ""}


@router.get("/console/api/settings")
def api_settings(request: Request):
    require_session(request)
    payload: dict[str, Any] = dict(store.get_many(SETTING_KEYS))
    payload["upstream_key"] = ""      # never send the secret back
    payload["upstream_key_state"] = _masked_key()
    payload["guard_fastpath"] = bool(int(_env("GUARD_FASTPATH", "0") or 0))
    payload["db_path"] = str(store.path)
    return payload


@router.post("/console/api/settings")
async def api_settings_save(request: Request):
    require_session(request)
    body = await request.json()
    updates = {}

    def number(key, low, high):
        value = float(body[key])
        if not low <= value <= high:
            raise HTTPException(422, "%s must be between %s and %s" % (key, low, high))
        updates[key] = str(value)

    if "guard_mode" in body:
        if body["guard_mode"] not in ("block", "monitor"):
            raise HTTPException(422, "guard_mode must be block or monitor")
        updates["guard_mode"] = body["guard_mode"]
    for key in ("guard_threshold_prompt_injection", "guard_threshold_hidden_instructions",
                "guard_threshold_secret_request"):
        if key in body:
            number(key, 0.01, 1.0)
    if "abuse_mode" in body:
        if body["abuse_mode"] not in ("block", "monitor"):
            raise HTTPException(422, "abuse_mode must be block or monitor")
        updates["abuse_mode"] = body["abuse_mode"]
    if "abuse_threshold" in body:
        number("abuse_threshold", 0.01, 1.0)
    if "abuse_block_ttl" in body:
        number("abuse_block_ttl", 5, 86400)
    if "abuse_enforce" in body:
        updates["abuse_enforce"] = "1" if body["abuse_enforce"] else "0"
    if "retention_days" in body:
        value = int(body["retention_days"])
        if not 1 <= value <= 365:
            raise HTTPException(422, "retention_days must be between 1 and 365")
        updates["retention_days"] = str(value)
    if "upstream_base" in body:
        base = str(body["upstream_base"] or "").strip()
        if base and not base.startswith(("http://", "https://")):
            raise HTTPException(422, "upstream_base must start with http:// or https://")
        updates["upstream_base"] = base
    if "upstream_model" in body:
        updates["upstream_model"] = str(body["upstream_model"] or "").strip()
    if body.get("upstream_key_clear"):
        updates["upstream_key"] = ""
    elif body.get("upstream_key"):
        updates["upstream_key"] = str(body["upstream_key"]).strip()
    if "agent_enabled" in body:
        updates["agent_enabled"] = "1" if body["agent_enabled"] else "0"
    if "agent_llm" in body:
        updates["agent_llm"] = "1" if body["agent_llm"] else "0"
    if "agent_mode" in body:
        if body["agent_mode"] not in ("monitor", "act"):
            raise HTTPException(422, "agent_mode must be monitor or act")
        updates["agent_mode"] = body["agent_mode"]
    if "agent_interval" in body:
        value = int(body["agent_interval"])
        if not 5 <= value <= 3600:
            raise HTTPException(422, "agent_interval must be between 5 and 3600 seconds")
        updates["agent_interval"] = str(value)

    if updates:
        store.set_many(updates)
        runtime.load()
    return {"ok": True, "updated": sorted(updates)}


@router.post("/console/api/purge")
def api_purge(request: Request):
    require_session(request)
    removed = store.purge(runtime.retention_days)
    return {"ok": True, "removed": removed}


@router.get("/console/api/overview")
def api_overview(request: Request):
    require_session(request)
    blocked = runtime.abuse.blocked_clients()
    payload = store.overview(runtime.retention_days, blocked_now=len(blocked))
    payload["blocked_clients"] = blocked
    return payload


@router.get("/console/api/decisions")
def api_decisions(request: Request, kind: str = "guard", limit: int = 50, label: str = ""):
    require_session(request)
    if kind not in ("guard", "abuse"):
        raise HTTPException(422, "kind must be guard or abuse")
    return {"decisions": store.decisions(kind, limit=limit, label=label or None)}


@router.get("/console/api/clients")
def api_clients(request: Request, top: int = 50):
    require_session(request)
    rows = runtime.abuse.clients(top)
    blocked = runtime.abuse.blocked_clients()
    for row in rows:
        row["blocked_for_s"] = blocked.get(row["client"], {}).get("remaining_s")
    return {"clients": rows}


@router.post("/console/api/unblock")
async def api_unblock(request: Request):
    require_session(request)
    body = await request.json()
    client = str(body.get("client") or "")
    if not client:
        raise HTTPException(422, "client is required")
    cleared = runtime.abuse.clear_block(client)
    if cleared:
        store.log_block(client, "unblock")
    return {"ok": True, "unblocked": cleared}


DEVICE_TTL = 30 * 86400


@router.get("/console/api/devices")
def api_devices(request: Request):
    require_session(request)
    rules = store.list_rules()
    blocked = runtime.abuse.blocked_clients()
    for rule in rules:
        rule["blocked_for_s"] = blocked.get(rule["device"], {}).get("remaining_s")
    return {"rules": rules, "blocked": len(blocked)}


@router.post("/console/api/devices/block")
async def api_device_block(request: Request):
    require_session(request)
    body = await request.json()
    device = str(body.get("device") or "").strip()
    if not device:
        raise HTTPException(422, "device is required")
    reason = str(body.get("reason") or "").strip()
    try:
        ttl = float(body.get("ttl") or 0)
    except (TypeError, ValueError):
        ttl = 0.0
    store.add_rule(device, reason, ttl)
    block_ttl = ttl if ttl > 0 else DEVICE_TTL
    runtime.abuse.block_client(device, block_ttl, category="device_rule", score=1.0)
    store.log_block(device, "block", "device_rule", 1.0, until=time.time() + block_ttl)
    return {"ok": True, "device": device, "permanent": ttl <= 0}


@router.post("/console/api/devices/unblock")
async def api_device_unblock(request: Request):
    require_session(request)
    body = await request.json()
    device = str(body.get("device") or "").strip()
    if not device:
        raise HTTPException(422, "device is required")
    removed = store.remove_rule(device)
    cleared = runtime.abuse.clear_block(device)
    if removed or cleared:
        store.log_block(device, "unblock")
    return {"ok": True, "device": device, "removed": removed or cleared}


@router.get("/console/api/agent")
def api_agent(request: Request):
    require_session(request)
    monitor = runtime.agent
    info = {
        "enabled": runtime.agent_enabled,
        "mode": runtime.agent_mode,
        "interval": runtime.agent_interval,
        "llm": runtime.agent_llm,
        "runs": 0,
        "actions_taken": 0,
        "last_run_age_s": None,
        "last_summary": "",
        "last_error": "",
    }
    if monitor is not None:
        info["runs"] = monitor.runs
        info["actions_taken"] = monitor.actions_taken
        info["last_summary"] = monitor.last_summary
        info["last_error"] = monitor.last_error
        if monitor.last_run:
            info["last_run_age_s"] = round(time.time() - monitor.last_run, 1)
    info["events"] = store.agent_events(100)
    return info


@router.post("/console/api/agent/run")
async def api_agent_run(request: Request):
    require_session(request)
    monitor = runtime.agent
    if monitor is None:
        raise HTTPException(503, "agent monitor is not running")
    result = await run_in_threadpool(monitor.run_once)
    return {"ok": True, "result": result}


@router.post("/console/api/agent/chat")
async def api_agent_chat(request: Request):
    require_session(request)
    monitor = runtime.agent
    if monitor is None:
        raise HTTPException(503, "agent monitor is not running")
    body = await request.json()
    message = str(body.get("message") or "")
    history = body.get("history") or []
    if not isinstance(history, list):
        history = []
    result = await run_in_threadpool(monitor.chat, message, history)
    return {"ok": True, **result}


@router.get("/console/api/system")
def api_system(request: Request):
    require_session(request)
    try:
        size = store.path.stat().st_size
    except OSError:
        size = 0
    return {
        "uptime_s": round(time.time() - STARTED_AT),
        "db_path": str(store.path),
        "db_bytes": size,
        "retention_days": runtime.retention_days,
        "guard_mode": runtime.guard_mode,
        "guard_thresholds": runtime.guard_thresholds,
        "abuse_mode": runtime.abuse_mode,
        "abuse_threshold": runtime.abuse_threshold,
        "abuse_enforce": runtime.abuse_enforce,
        "abuse_block_ttl": runtime.abuse_block_ttl,
        "upstream_base": runtime.upstream_base,
        "upstream_model": runtime.upstream_model,
        "upstream_key": _masked_key(),
        "blocked_clients": runtime.abuse.blocked_clients(),
        "agent_enabled": runtime.agent_enabled,
        "agent_mode": runtime.agent_mode,
        "agent_interval": runtime.agent_interval,
        "agent_llm": runtime.agent_llm,
    }
