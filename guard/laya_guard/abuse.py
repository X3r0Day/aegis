"""API abuse detection from gateway telemetry, metadata only, no bodies.

Events look like {"ts": ..., "ip": ..., "method": ..., "path": ...,
"status": ..., "user_agent": ..., "api_key_id": ..., ...} and get folded into
per client sliding windows. Laya only runs when cheap triggers fire and the
client's cooldown is up, never once per request.
"""
import math
import re
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import DEFAULT_MODELS_DIR
from .model import get_runner

_NUMERIC_SUFFIX = re.compile(r"^(.*?/)(\d{1,18})$")
_TOOL_MARKERS = (
    "python-requests", "python-urllib", "httpx", "aiohttp", "scrapy", "curl/",
    "wget/", "go-http-client", "java/", "okhttp", "node-fetch", "axios",
    "postmanruntime", "insomnia", "libwww-perl", "powershell", "sqlmap",
    "nikto", "nmap", "masscan",
)
_BROWSER_MARKERS = ("mozilla/", "chrome/", "safari/", "firefox/")
_SCANNER_MARKERS = ("sqlmap", "nikto", "nmap", "masscan", "wpscan", "acunetix", "netsparker")


def _now():
    return time.time()


def _human_age(seconds):
    seconds = max(0.0, seconds)
    if seconds < 90:
        return "%.0fs" % seconds
    if seconds < 5400:
        return "%.0fm" % (seconds / 60)
    if seconds < 172800:
        return "%.1fh" % (seconds / 3600)
    return "%.1fd" % (seconds / 86400)


@dataclass
class Event:
    ts: float
    ip: str = "0.0.0.0"
    method: str = "GET"
    path: str = "/"
    route: str | None = None
    status: int = 0
    latency_ms: float | None = None
    bytes_in: int = 0
    bytes_out: int = 0
    api_key_id: str | None = None
    user_agent: str | None = None
    token_id: str | None = None
    username: str | None = None
    asn: str | None = None
    country: str | None = None
    datacenter: bool | None = None
    in_flight: int | None = None
    client_age_s: float | None = None
    prior_requests_24h: int | None = None
    prior_error_rate: float | None = None

    @classmethod
    def from_dict(cls, raw):
        ts = raw.get("ts", raw.get("timestamp"))
        if ts is None:
            raise ValueError("event is missing 'ts'")
        ts = float(ts)
        if ts > 1e12:          # someone sent milliseconds
            ts /= 1000.0
        return cls(
            ts=ts,
            ip=str(raw.get("ip") or raw.get("client_ip") or "0.0.0.0"),
            method=str(raw.get("method") or "GET").upper(),
            path=str(raw.get("path") or raw.get("url") or "/"),
            route=raw.get("route"),
            status=int(raw.get("status") or 0),
            latency_ms=_opt_float(raw.get("latency_ms")),
            bytes_in=int(raw.get("bytes_in") or 0),
            bytes_out=int(raw.get("bytes_out") or 0),
            api_key_id=raw.get("api_key_id") or raw.get("key_id"),
            user_agent=raw.get("user_agent") or raw.get("ua"),
            token_id=raw.get("token_id"),
            username=raw.get("username") or raw.get("user"),
            asn=str(raw["asn"]) if raw.get("asn") is not None else None,
            country=raw.get("country"),
            datacenter=raw.get("datacenter"),
            in_flight=int(raw["in_flight"]) if raw.get("in_flight") is not None else None,
            client_age_s=_opt_float(raw.get("client_age_s") or raw.get("client_age")),
            prior_requests_24h=(int(raw["prior_requests_24h"]) if raw.get("prior_requests_24h") is not None else None),
            prior_error_rate=_opt_float(raw.get("prior_error_rate")),
        )

    @property
    def client_key(self):
        return str(self.api_key_id) if self.api_key_id else self.ip

    @property
    def endpoint_key(self):
        # gateway route if we have one, otherwise mask the id in the path
        if self.route:
            return self.route
        match = _NUMERIC_SUFFIX.match(self.path)
        return match.group(1) + "{id}" if match else self.path


def _opt_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class ClientState:
    def __init__(self):
        self.first_seen = 0.0
        self.last_seen = 0.0
        self.total = 0
        self.ip = ""
        self.asn = None
        self.country = None
        self.datacenter = None
        self.api_key_id = None
        self.client_age_s = None
        self.prior_requests_24h = None
        self.prior_error_rate = None
        self.seconds = {}              # second bucket -> count, 1h
        self.minutes = {}              # minute bucket -> count, 24h
        self.peak_1s = 0
        self.path_counts = {}
        self.endpoint_counts = {}
        self.endpoint_status = {}
        self.endpoint_paths = {}
        self.endpoint_path_overflow = set()
        self.path_first_seen = {}
        self.status = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0}
        self.status_exact = {}
        self.methods = {}
        self.auth_fail = 0
        self.auth_fail_on_auth = 0
        self.bytes_in = 0
        self.bytes_out = 0
        self.max_bytes_in = 0
        self.max_bytes_out = 0
        self.ua_seen = {}
        self.ua_missing = 0
        self.timestamps = deque(maxlen=256)
        self.id_events = deque(maxlen=500)
        self.max_in_flight = None
        self.api_keys = set()
        self.tokens = set()
        self.usernames = set()
        self.usernames_total = 0
        self.last_eval_ts = 0.0
        self.last_verdict = None

    def add(self, event):
        if not self.first_seen:
            # gateways that know the client age can backdate first_seen
            self.first_seen = event.ts - (event.client_age_s or 0.0)
            self.ip = event.ip
        self.last_seen = max(self.last_seen, event.ts)
        self.total += 1
        self.ip = event.ip
        for attr in ("asn", "country", "datacenter", "client_age_s",
                     "prior_requests_24h", "prior_error_rate"):
            value = getattr(event, attr)
            if value is not None:
                setattr(self, attr, value)
        if event.api_key_id:
            self.api_key_id = event.api_key_id
            if len(self.api_keys) < 50:
                self.api_keys.add(event.api_key_id)
        if event.token_id and len(self.tokens) < 500:
            self.tokens.add(event.token_id)
        if event.username:
            self.usernames_total += 1
            if len(self.usernames) < 500:
                self.usernames.add(event.username)

        sec = int(event.ts)
        self.seconds[sec] = self.seconds.get(sec, 0) + 1
        self.minutes[int(event.ts // 60)] = self.minutes.get(int(event.ts // 60), 0) + 1
        self.peak_1s = max(self.peak_1s, self.seconds[sec])

        self._bump(self.path_counts, event.path, cap=3000)
        self.path_first_seen.setdefault(event.path, event.ts)
        endpoint = event.endpoint_key
        self._bump(self.endpoint_counts, endpoint, cap=500)
        status_map = self.endpoint_status.setdefault(endpoint, {})
        status_map[event.status] = status_map.get(event.status, 0) + 1
        paths = self.endpoint_paths.setdefault(endpoint, set())
        if endpoint not in self.endpoint_path_overflow:
            if len(paths) < 3000:
                paths.add(event.path)
            else:
                self.endpoint_path_overflow.add(endpoint)
                paths.clear()

        bucket = "%dxx" % (event.status // 100) if 100 <= event.status < 600 else "other"
        if bucket in self.status:
            self.status[bucket] += 1
        self.status_exact[event.status] = self.status_exact.get(event.status, 0) + 1
        if event.status in (401, 403):
            self.auth_fail += 1
            low = event.path.lower()
            if any(tok in low for tok in ("login", "auth", "token", "session", "signin", "sign-in")):
                self.auth_fail_on_auth += 1

        self.methods[event.method] = self.methods.get(event.method, 0) + 1
        self.bytes_in += event.bytes_in
        self.bytes_out += event.bytes_out
        self.max_bytes_in = max(self.max_bytes_in, event.bytes_in)
        self.max_bytes_out = max(self.max_bytes_out, event.bytes_out)

        if event.user_agent:
            self._bump(self.ua_seen, event.user_agent[:160], cap=50)
        else:
            self.ua_missing += 1

        self.timestamps.append(event.ts)
        match = _NUMERIC_SUFFIX.match(event.path)
        if match:
            self.id_events.append((match.group(1), int(match.group(2))))

        if event.in_flight is not None:
            self.max_in_flight = max(self.max_in_flight or 0, event.in_flight)

    @staticmethod
    def _bump(mapping, key, cap):
        # keep a bounded number of distinct keys, everything else goes to (other)
        if key in mapping:
            mapping[key] += 1
        elif len(mapping) < cap:
            mapping[key] = 1
        else:
            mapping["(other)"] = mapping.get("(other)", 0) + 1

    def prune(self, now):
        sec_floor = int(now) - 3600
        for key in [k for k in self.seconds if k < sec_floor]:
            del self.seconds[key]
        min_floor = int(now // 60) - 1440
        for key in [k for k in self.minutes if k < min_floor]:
            del self.minutes[key]

    def count_window(self, now, window_s):
        if window_s <= 3600:
            floor = int(now) - int(window_s) + 1
            return sum(v for k, v in self.seconds.items() if k >= floor)
        floor = int((now - window_s) // 60)
        return sum(v for k, v in self.minutes.items() if k >= floor)

    def rate_per_minute(self, now, window_s=60.0):
        return self.count_window(now, window_s) * (60.0 / window_s)

    def status_rates(self):
        total = sum(self.status.values()) or 1
        return {k: v / total for k, v in self.status.items()}

    def new_paths_window(self, now, window_s):
        floor = now - window_s
        return sum(1 for ts in self.path_first_seen.values() if ts >= floor)

    def inter_arrival(self):
        # (mean, min, max, CV, n) in seconds
        stamps = sorted(self.timestamps)
        if len(stamps) < 8:
            return None
        diffs = [b - a for a, b in zip(stamps, stamps[1:]) if b > a]
        if len(diffs) < 5:
            return None
        mean = sum(diffs) / len(diffs)
        if mean <= 0:
            return None
        var = sum((d - mean) ** 2 for d in diffs) / len(diffs)
        return mean, min(diffs), max(diffs), math.sqrt(var) / mean, len(diffs)

    def id_step_score(self):
        last = {}
        hits = checks = 0
        first = last_ident = None
        for prefix, ident in self.id_events:
            if prefix in last:
                checks += 1
                if ident == last[prefix] + 1:
                    hits += 1
            else:
                first = ident
            last[prefix] = ident
            last_ident = ident
        self._id_span = (first, last_ident) if checks else None
        return (hits / checks) if checks >= 5 else None

    def id_span(self):
        return getattr(self, "_id_span", None)

    def tool_uas(self):
        found = []
        for ua in self.ua_seen:
            low = ua.lower()
            if any(m in low for m in _TOOL_MARKERS):
                found.append(ua[:60])
        return found[:3]

    def browser_like(self):
        return any(any(m in ua.lower() for m in _BROWSER_MARKERS) for ua in self.ua_seen)


class TrafficAggregator:
    def __init__(self, max_clients=10000):
        self.clients = {}
        self.max_clients = max_clients

    def observe(self, events):
        touched = []
        for event in events:
            key = event.client_key
            state = self.clients.get(key)
            if state is None:
                if len(self.clients) >= self.max_clients:
                    oldest = min(self.clients, key=lambda k: self.clients[k].last_seen)
                    del self.clients[oldest]
                state = self.clients[key] = ClientState()
            state.add(event)
            touched.append(key)
        return touched

    def prune(self, now=None):
        now = now or _now()
        for state in self.clients.values():
            state.prune(now)


@dataclass
class AbuseConfig:
    # typed-decisions measured best on the synthetic suite: benign abuse score
    # stays under 0.35, attacks come in over 0.55. recalibrate threshold on
    # real traffic before switching mode to block
    models_dir: Path = DEFAULT_MODELS_DIR
    model: str = "typed-decisions"
    threshold: float = 0.45            # mean of is_abuse and true_positive
    severity_floor: float = 1.0        # severity gate for enforcement
    mode: str = "monitor"
    cooldown_s: float = 60.0           # min gap between Laya evals per client
    state_key: str = "traffic"
    features_window_s: float = 60.0
    device: str | None = None


@dataclass
class AbuseVerdict:
    client: str
    decision: str
    enforced: bool
    mode: str
    threshold: float
    abuse_score: float
    is_abuse: float
    true_positive: float
    severity: float
    category: str
    pattern: str
    action: str                    # what Laya suggested
    recommended_action: str        # what the policy does with it
    probabilities: dict
    confidences: dict
    model: str
    routing: dict | None
    latency_ms: float
    narrative: str
    features: dict
    triggered_by: list = field(default_factory=list)
    error: str | None = None

    @property
    def label(self):
        if self.error:
            return "error"
        if self.decision == "block":
            return "block" if self.enforced else "would-block"
        return "allow"

    def to_dict(self):
        d = asdict(self)
        d["label"] = self.label
        return d


def abuse_questions():
    return {
        "category": {
            "type": "choice",
            "instructions": "Looking only at `traffic`, what kind of API client behaviour is described?",
            "criteria": {
                "normal_client": "ordinary human or app traffic: varied endpoints, low rate, mixed but mostly successful responses, browser user agent",
                "integration_burst": "legitimate automation: a steady poller or batch job, one endpoint family, stable API key, almost all 2xx, known long-lived client",
                "scraping_enumeration": "data harvesting: walking many distinct resources or sequential ids at speed, high unique-resource ratio, many 404s or mass 2xx on /resource/{id}",
                "credential_stuffing": "repeated login, token or password attempts, most rejected with 401/403, many distinct usernames or credentials tried",
                "rate_abuse_dos": "volume or concurrency so high it can degrade the service: flood or sustained burst on the same small set of endpoints",
                "parameter_fuzzing": "malformed, random or unexpected paths, methods and payloads; probing for vulnerabilities; high 4xx/5xx rate with scanner user agent",
            },
        },
        "severity": {
            "type": "score",
            "instructions": "How severe is the risk to the API from the client in `traffic`?",
            "criteria": [
                "benign: normal use or clearly legitimate known automation",
                "noisy: heavy but harmless, no sign of extraction or attack",
                "abusive: extracting data or degrading service, act now",
                "severe: active attack or outage risk, immediate action",
            ],
        },
        "is_abuse": {
            "type": "noul",
            "instructions": "Is the client in `traffic` abusing the API (scraping, credential stuffing, fuzzing, or rate abuse) instead of using it legitimately?",
        },
        "true_positive": {
            "type": "noul",
            "instructions": "Is the client in `traffic` a true security concern (attacking, extracting data, or abusing the API) rather than benign activity?",
        },
        "action": {
            "type": "choice",
            "instructions": "What should the API gateway do with this client right now, based on `traffic`?",
            "criteria": {
                "allow": "no action, normal service",
                "throttle": "apply strict rate limits temporarily",
                "block": "reject requests from this client",
                "review": "flag for a human to inspect",
            },
        },
    }


class AbuseDetector:
    def __init__(self, config=None, aggregator=None):
        self.cfg = config or AbuseConfig()
        self.agg = aggregator or TrafficAggregator()
        self.questions = abuse_questions()
        self._runner = None
        self._load_lock = threading.Lock()
        self._infer_lock = threading.Lock()
        self._data_lock = threading.RLock()   # ingest runs on the loop, evals on threads
        self._blocks = {}

    def block_client(self, client_key, ttl_s, *, category="", score=0.0):
        self._blocks[client_key] = {"until": _now() + ttl_s, "category": category, "score": score}

    def block_state(self, client_key):
        block = self._blocks.get(client_key)
        if not block:
            return None
        remaining = block["until"] - _now()
        if remaining <= 0:
            self._blocks.pop(client_key, None)
            return None
        return {"category": block["category"], "score": block["score"], "remaining_s": round(remaining, 1)}

    def clear_block(self, client_key):
        return self._blocks.pop(client_key, None) is not None

    def reset_client(self, client_key):
        # bench helper: start a scenario client from scratch
        with self._data_lock:
            removed = self.agg.clients.pop(client_key, None) is not None
        self.clear_block(client_key)
        return removed

    def blocked_clients(self):
        out = {}
        for key in list(self._blocks):
            state = self.block_state(key)
            if state:
                out[key] = state
        return out

    def _ensure_runner(self):
        with self._load_lock:
            if self._runner is None:
                self._runner = get_runner(self.cfg.models_dir, self.cfg.device)
            return self._runner

    def warm(self):
        self._ensure_runner().load("english")
        return self

    @property
    def loaded_models(self):
        return self._runner.loaded if self._runner is not None else []

    def observe(self, raw_events):
        events = [Event.from_dict(e) for e in raw_events]
        with self._data_lock:
            return self.agg.observe(events)

    def trigger_reasons(self, client_key, now=None):
        now = now or _now()
        with self._data_lock:
            return self._trigger_reasons(self.agg.clients[client_key], now)

    def triggered_clients(self, now=None):
        now = now or _now()
        out = []
        with self._data_lock:
            for key, state in self.agg.clients.items():
                if now - state.last_eval_ts < self.cfg.cooldown_s:
                    continue
                if self._trigger_reasons(state, now):
                    out.append(key)
        return out

    def _trigger_reasons(self, state, now):
        n60 = state.count_window(now, 60)
        if n60 < 30:
            return []
        reasons = []
        rate = n60 / 60.0
        own_rate = state.rate_per_minute(now, 86400)
        if rate >= 5 and (own_rate < 1 or rate >= 5 * own_rate):
            reasons.append("rate_spike")
        if state.auth_fail >= 15:
            reasons.append("auth_failures")
        if state.status["4xx"] >= 30:
            reasons.append("error_probe")
        if state.new_paths_window(now, 60) >= 40:
            reasons.append("new_paths")
        if state.peak_1s >= 25:
            reasons.append("burst")
        step = state.id_step_score()
        if step is not None and step >= 0.5 and n60 >= 40:
            reasons.append("sequential_ids")
        if (state.max_in_flight or 0) >= 10:
            reasons.append("concurrency")
        return reasons

    def evaluate_triggered(self, mode=None, now=None):
        now = now or _now()
        verdicts = []
        for key in self.triggered_clients(now):
            reasons = self._trigger_reasons(self.agg.clients[key], now)
            verdict = self.evaluate(key, force=True, mode=mode, now=now, triggered_by=reasons)
            if verdict:
                verdicts.append(verdict)
        return verdicts

    def features(self, client_key, now=None):
        now = now or _now()
        with self._data_lock:
            return self._features_unlocked(client_key, now)

    def _features_unlocked(self, client_key, now):
        state = self.agg.clients[client_key]
        window = self.cfg.features_window_s
        n60 = state.count_window(now, window)
        total = state.total or 1
        # a client younger than the window gets rated over the span we saw,
        # otherwise short runs come out understated
        observed = max(1.0, min(window, now - state.first_seen))
        rate60 = n60 / observed
        own_rate = state.rate_per_minute(now, 86400)
        population = self._population(now)
        unique_new = state.new_paths_window(now, window)
        unique_ratio = min(1.0, unique_new / n60) if n60 else 0.0
        step = state.id_step_score()
        inter = state.inter_arrival()

        endpoints = []
        for endpoint, count in sorted(state.endpoint_counts.items(), key=lambda kv: -kv[1])[:2]:
            statuses = sorted(state.endpoint_status.get(endpoint, {}).items(), key=lambda kv: -kv[1])[:3]
            endpoints.append({
                "endpoint": endpoint,
                "count": count,
                "share": count / total,
                "statuses": statuses,
                "distinct_paths": len(state.endpoint_paths.get(endpoint, ())),
                "overflow": endpoint in state.endpoint_path_overflow,
            })

        f = {
            "client": client_key,
            "ip": state.ip,
            "first_seen_age_s": now - state.first_seen,
            "client_age_s": state.client_age_s,
            "prior_requests_24h": state.prior_requests_24h,
            "prior_error_rate": state.prior_error_rate,
            "total_requests": state.total,
            "n_window": n60,
            "window_s": window,
            "observed_s": observed,
            "rate_per_s": rate60,
            "rate_per_min": rate60 * 60.0,
            "peak_1s": state.peak_1s,
            "own_rate_per_min": own_rate,
            "population": population,
            "distinct_paths": len(state.path_counts),
            "endpoints": endpoints,
            "new_paths_window": unique_new,
            "unique_ratio": unique_ratio,
            "id_step_score": step,
            "id_span": state.id_span(),
            "status_counts": dict(state.status),
            "status_rates": state.status_rates(),
            "status_exact": {str(k): v for k, v in sorted(state.status_exact.items())
                             if k in (200, 201, 400, 401, 403, 404, 429, 500)},
            "auth_fail": state.auth_fail,
            "auth_fail_on_auth": state.auth_fail_on_auth,
            "methods": sorted(state.methods.items(), key=lambda kv: -kv[1])[:3],
            "inter_arrival": inter,
            "max_in_flight": state.max_in_flight,
            "bytes_in_total": state.bytes_in,
            "bytes_out_total": state.bytes_out,
            "max_bytes_in": state.max_bytes_in,
            "max_bytes_out": state.max_bytes_out,
            "ua_distinct": len(state.ua_seen),
            "ua_top": sorted(state.ua_seen.items(), key=lambda kv: -kv[1])[:2],
            "ua_missing": state.ua_missing,
            "tool_uas": state.tool_uas(),
            "browser_like": state.browser_like(),
            "api_keys": len(state.api_keys),
            "tokens": len(state.tokens),
            "usernames": len(state.usernames),
            "usernames_total": state.usernames_total,
            "asn": state.asn,
            "country": state.country,
            "datacenter": state.datacenter,
        }
        f["flags"] = self._flags(state, now, f)
        f["pattern"] = self._pattern(state, now, f)
        return f

    def _population(self, now):
        rates = [s.rate_per_minute(now, 60) for s in self.agg.clients.values()
                 if now - s.last_seen <= 300]
        if not rates:
            return {"p50": 0.0, "p95": 0.0, "clients": 0}
        rates.sort()
        return {
            "p50": rates[len(rates) // 2],
            "p95": rates[min(len(rates) - 1, int(len(rates) * 0.95))],
            "clients": len(rates),
        }

    @staticmethod
    def _flags(state, now, f):
        flags = []
        n60 = f["n_window"]
        rate60 = f["rate_per_s"]
        if rate60 >= 5 and (f["own_rate_per_min"] < 1 or rate60 * 60 >= 5 * f["own_rate_per_min"]):
            flags.append("rate_spike(>=5/s and >=5x own average)")
        if f["population"]["p95"] and f["rate_per_min"] >= 5 * max(f["population"]["p95"], 1.0):
            flags.append("population_outlier(>=5x p95)")
        if n60 >= 40 and f["unique_ratio"] >= 0.9:
            flags.append("enumeration_like(unique_ratio>=0.9 over >=40 requests)")
        if f["id_step_score"] is not None and f["id_step_score"] >= 0.5:
            flags.append("sequential_ids(id_step=%.2f>=0.5)" % f["id_step_score"])
        if f["status_rates"].get("4xx", 0) >= 0.3:
            flags.append("probing(4xx>=30%)")
        if f["auth_fail_on_auth"] >= 15:
            flags.append("auth_attack(>=15 rejected attempts on auth endpoints)")
        if state.peak_1s >= 25 or (state.max_in_flight or 0) >= 10:
            flags.append("burst(peak_1s>=25 or concurrency>=10)")
        inter = f["inter_arrival"]
        if inter and inter[4] >= 30 and inter[3] <= 0.1:
            flags.append("metronomic(CV<=0.1)")
        if f["client_age_s"] is not None and f["client_age_s"] <= 300 and n60 >= 100:
            flags.append("new_client_volume(<5m old, >=100 requests/min)")
        if state.max_bytes_out >= 5_000_000 or state.max_bytes_in >= 1_000_000:
            flags.append("large_payload(>=5MB response or >=1MB request)")
        if state.ua_missing > state.total // 2 or f["tool_uas"]:
            flags.append("automation(tool UA or missing UA)")
        if f["usernames_total"] >= 20 and f["usernames"] >= 15:
            flags.append("many_usernames(>=15 distinct tried)")
        return flags

    @staticmethod
    def _pattern(state, now, f):
        # the dominant signature, named with the same words as the category options
        sr = f["status_rates"]
        inter = f["inter_arrival"]
        if f["auth_fail_on_auth"] >= 15 and sr.get("4xx", 0) >= 0.3:
            return "credential_stuffing (repeated login attempts, most rejected)"
        if f["id_step_score"] is not None and f["id_step_score"] >= 0.5 and f["unique_ratio"] >= 0.5:
            return "scraping_enumeration (distinct resource ids walked in order at speed)"
        if f["unique_ratio"] >= 0.8 and f["n_window"] >= 80:
            return "scraping_enumeration (many distinct resources, little repetition)"
        if state.peak_1s >= 25 or (state.max_in_flight or 0) >= 10:
            return "rate_abuse_dos (bursts with high concurrency on a small endpoint set)"
        if sr.get("4xx", 0) + sr.get("5xx", 0) >= 0.3 and (f["tool_uas"] or state.ua_missing):
            return "parameter_fuzzing (random or unexpected requests, high error rate)"
        steady = inter is not None and inter[3] <= 0.3
        known = f["client_age_s"] is not None and f["client_age_s"] > 86400
        success = sr.get("2xx", 0) >= 0.95
        if steady and success and (known or f["api_keys"] > 0) and len(state.endpoint_counts) <= 3:
            return "integration_polling (steady successful polling by a known keyed client)"
        if f["rate_per_s"] < 0.5 and len(state.endpoint_counts) >= 4:
            return "normal_use (varied endpoints at a human pace)"
        return "mixed_use (no single dominant signature)"

    def render_state(self, f):
        # prose beats dense tables here. the checkpoint read structured number
        # soup close to chance while this wording separates cleanly
        s = []
        if (f["client_age_s"] or 0) > 86400:
            s.append("This client has used the API for %d days." % (f["client_age_s"] / 86400))
        else:
            s.append("This is a brand-new client, first seen %s ago."
                     % _human_age(max(0.0, f["first_seen_age_s"])))
        if f["prior_requests_24h"] is not None:
            s.append("In the prior 24 hours it made %s requests with a %.1f%% error rate."
                     % ("{:,}".format(f["prior_requests_24h"]), 100 * (f["prior_error_rate"] or 0)))

        if f["datacenter"]:
            where = "a datacenter IP"
        elif f["datacenter"] is False:
            where = "an end-user connection"
        else:
            where = "an IP address"
        src = "Traffic comes from %s" % where
        if f["country"]:
            src += " in %s" % f["country"]
        if f["asn"]:
            src += " (ASN %s)" % f["asn"]
        s.append(src + ".")
        s.append("Traffic signature: %s." % f["pattern"])

        ep = f["endpoints"][0] if f["endpoints"] else None
        if ep:
            span = int(round(min(f["window_s"], f["observed_s"])))
            s.append("It made %d requests in the last %ds (%.1f per second, peak %d per second), "
                     "%d%% of them to %s."
                     % (f["n_window"], span, f["rate_per_s"], f["peak_1s"], int(ep["share"] * 100), ep["endpoint"]))
        pop = f["population"]
        ratio = f["rate_per_min"] / pop["p95"] if pop["p95"] >= 0.5 else 0.0
        if ratio >= 2:
            s.append("That is %.0f times the population p95 of %.1f requests per minute."
                     % (ratio, pop["p95"]))

        span = f["id_span"]
        if f["id_step_score"] is not None and f["id_step_score"] >= 0.5 and span:
            s.append("It walked through %d different resources one after another in exact numeric order, "
                     "requesting ids from %d to %d like a list being scraped."
                     % (f["new_paths_window"], span[0], span[1]))
        elif f["unique_ratio"] >= 0.8:
            s.append("Almost every request hit a different resource: %d distinct resources with almost no repetition."
                     % f["distinct_paths"])

        sr = f["status_rates"]
        if f["auth_fail_on_auth"] >= 15:
            s.append("%d of %d authentication attempts were rejected with 401 or 403."
                     % (f["auth_fail_on_auth"], f["total_requests"]))
            if f["usernames"] >= 10:
                s.append("Each attempt used a different username (%d distinct usernames tried)." % f["usernames"])
        elif f["auth_fail"] >= 15:
            s.append("The API rejected %d requests with 401 or 403." % f["auth_fail"])
        if f["auth_fail_on_auth"] < 15 and sr.get("4xx", 0) >= 0.3:
            s.append("About %d%% of requests failed with client errors (%d were 404), "
                     "as if endpoints were being probed."
                     % (int(sr["4xx"] * 100), f["status_exact"].get("404", 0)))
            examples = [e["endpoint"] for e in f["endpoints"]][:3]
            if examples:
                s.append("Examples of requested paths: %s." % ", ".join(examples))
        elif sr.get("2xx", 0) >= 0.95:
            s.append("Almost every request succeeded (%d%% got 2xx responses)." % int(sr["2xx"] * 100))
        if f["status_exact"].get("429", 0) or sr.get("5xx", 0) >= 0.05:
            s.append("The service pushed back: %d requests were rate-limited and %d%% were server errors."
                     % (f["status_exact"].get("429", 0), int(sr.get("5xx", 0) * 100)))

        inter = f["inter_arrival"]
        if inter:
            rhythm = ("with a robotic, machine-like rhythm (every %.0f milliseconds)" % (inter[0] * 1000)) \
                if inter[3] <= 0.1 else "with irregular, human-like gaps between requests"
            extra = " and up to %d requests in flight at once" % f["max_in_flight"] if f["max_in_flight"] else ""
            s.append("Requests arrive %s%s." % (rhythm, extra))
        if f["api_keys"]:
            s.append("It authenticates with a valid API key.")
        elif f["usernames_total"] == 0:
            s.append("It presents no API key and no credentials.")
        ua = f["ua_top"][0][0] if f["ua_top"] else None
        if ua:
            low = ua.lower()
            if any(m in low for m in _SCANNER_MARKERS):
                label = "a known attack scanner"
            elif f["tool_uas"]:
                label = "an automation library"
            elif f["browser_like"]:
                label = "a browser"
            else:
                label = "a script"
            s.append('It identifies itself as "%s" (%s).' % (ua, label))
        return " ".join(s)

    def evaluate(self, client_key, *, force=False, mode=None, now=None, triggered_by=None):
        cfg = self.cfg
        now = now or _now()
        with self._data_lock:
            if client_key not in self.agg.clients:
                raise KeyError("unknown client %r" % client_key)
            state = self.agg.clients[client_key]
            if not force and now - state.last_eval_ts < cfg.cooldown_s:
                return None
            f = self.features(client_key, now)
            narrative = self.render_state(f)
            state.last_eval_ts = now
        mode = mode or cfg.mode

        started = time.perf_counter()
        try:
            with self._infer_lock:
                result = self._ensure_runner().predict(
                    {cfg.state_key: narrative},
                    self.questions,
                    model=None if cfg.model == "auto" else cfg.model,
                )
            answers = result["answers"]
            routing = result.get("routing") or {}
        except Exception as exc:
            verdict = AbuseVerdict(
                client=client_key, decision="allow", enforced=False, mode=mode,
                threshold=cfg.threshold, abuse_score=0.0, is_abuse=0.0, true_positive=0.0,
                severity=0.0, category="unknown", pattern=f.get("pattern", ""),
                action="review", recommended_action="review",
                probabilities={}, confidences={}, model=cfg.model, routing=None,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                narrative=narrative, features=f, triggered_by=triggered_by or [],
                error="%s: %s" % (type(exc).__name__, exc),
            )
            with self._data_lock:
                state.last_verdict = verdict.to_dict()
            return verdict

        is_abuse = float(answers["is_abuse"]["noul"])
        true_positive = float(answers["true_positive"]["noul"])
        severity = float(answers["severity"]["score"])
        category = str(answers["category"]["choice"])
        action = str(answers["action"]["choice"])
        abuse_score = (is_abuse + true_positive) / 2.0
        blocked = abuse_score >= cfg.threshold and severity >= cfg.severity_floor
        if blocked:
            recommended = "block" if severity >= 2.0 else "throttle"
        elif abuse_score >= 0.8 * cfg.threshold or severity >= cfg.severity_floor:
            recommended = "review"
        else:
            recommended = "allow"

        verdict = AbuseVerdict(
            client=client_key,
            decision="block" if blocked else "allow",
            enforced=blocked and mode == "block",
            mode=mode,
            threshold=cfg.threshold,
            abuse_score=round(abuse_score, 4),
            is_abuse=round(is_abuse, 4),
            true_positive=round(true_positive, 4),
            severity=round(severity, 4),
            category=category,
            pattern=f.get("pattern", ""),
            action=action,
            recommended_action=recommended,
            probabilities={
                "is_abuse": round(is_abuse, 4),
                "true_positive": round(true_positive, 4),
                "abuse_score": round(abuse_score, 4),
                "severity_expected": round(severity, 4),
                "category": category,
                "action": action,
            },
            confidences={
                "is_abuse": answers["is_abuse"]["confidence"],
                "true_positive": answers["true_positive"]["confidence"],
                "severity": answers["severity"]["confidence"],
                "category": answers["category"]["confidence"],
                "action": answers["action"]["confidence"],
            },
            model=routing.get("model") or cfg.model,
            routing=routing or None,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            narrative=narrative,
            features=f,
            triggered_by=triggered_by or [],
        )
        with self._data_lock:
            state.last_verdict = verdict.to_dict()
        return verdict

    def check_events(self, raw_events, mode=None):
        # standalone batch, temp aggregator, request files and the bench use this
        temp = AbuseDetector(self.cfg, aggregator=TrafficAggregator())
        temp.observe(raw_events)
        now = max((s.last_seen for s in temp.agg.clients.values()), default=_now())
        verdicts = []
        for key in list(temp.agg.clients):
            reasons = temp.trigger_reasons(key, now)
            verdict = temp.evaluate(key, force=True, mode=mode, now=now, triggered_by=reasons)
            if verdict:
                verdicts.append(verdict)
        return verdicts

    def clients(self, top=20, now=None):
        now = now or _now()
        rows = []
        with self._data_lock:
            for key, state in self.agg.clients.items():
                verdict = state.last_verdict or {}
                block = self.block_state(key)
                rows.append({
                    "client": key,
                    "ip": state.ip,
                    "total": state.total,
                    "rate_per_min": round(state.rate_per_minute(now, 60), 2),
                    "last_seen_age_s": round(now - state.last_seen, 1),
                    "label": verdict.get("label"),
                    "is_abuse": verdict.get("is_abuse"),
                    "category": verdict.get("category"),
                    "pattern": verdict.get("pattern"),
                    "last_eval_age_s": round(now - state.last_eval_ts, 1) if state.last_eval_ts else None,
                    "blocked_for_s": block["remaining_s"] if block else None,
                })
        rows.sort(key=lambda r: (-(r["is_abuse"] or 0), -r["rate_per_min"]))
        return rows[:max(1, min(top, 200))]
