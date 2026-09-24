"""SQLite store for the console: settings, admin login, decision history.

Single file database next to the code by default (guard/data/aegis.db),
override with AEGIS_DB. Everything the console shows comes from here, the
JSONL logs stay as a raw export.
"""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "data/aegis.db"
OLD_DB = HERE / "data/layagate.db"

PBKDF2_ROUNDS = 200_000

SCHEMA = """
create table if not exists settings (
    key   text primary key,
    value text not null
);
create table if not exists guard_decisions (
    id            integer primary key autoincrement,
    ts            real not null,
    endpoint      text,
    label         text,
    decision      text,
    mode          text,
    threshold     real,
    model         text,
    latency_ms    real,
    note          text,
    probabilities text,
    triggers      text,
    text_sha256   text,
    excerpt       text
);
create table if not exists abuse_decisions (
    id                 integer primary key autoincrement,
    ts                 real not null,
    endpoint           text,
    client             text,
    label              text,
    decision           text,
    mode               text,
    threshold          real,
    abuse_score        real,
    severity           real,
    category           text,
    pattern            text,
    recommended_action text,
    model              text,
    latency_ms         real,
    triggers           text,
    narrative          text,
    error              text
);
create table if not exists block_events (
    id       integer primary key autoincrement,
    ts       real not null,
    client   text not null,
    action   text not null,
    category text,
    score    real,
    until    real
);
create table if not exists device_rules (
    device     text primary key,
    reason     text,
    created_at real not null,
    expires_at real
);
create table if not exists agent_events (
    id     integer primary key autoincrement,
    ts     real not null,
    mode   text,
    source text,
    action text,
    target text,
    reason text,
    detail text
);
create index if not exists idx_guard_ts on guard_decisions(ts);
create index if not exists idx_abuse_ts on abuse_decisions(ts);
create index if not exists idx_blocks_ts on block_events(ts);
create index if not exists idx_agent_ts on agent_events(ts);
"""

SETTING_DEFAULTS = {
    "guard_mode": "block",
    "guard_threshold_prompt_injection": "0.8",
    "guard_threshold_hidden_instructions": "0.6",
    "guard_threshold_secret_request": "0.85",
    "abuse_mode": "monitor",
    "abuse_threshold": "0.45",
    "abuse_block_ttl": "60",
    "abuse_enforce": "1",
    "retention_days": "7",
    "upstream_base": "",
    "upstream_model": "",
    "upstream_key": "",
    "agent_enabled": "1",
    "agent_mode": "act",
    "agent_interval": "20",
    "agent_llm": "1",
}


def db_path():
    return Path(os.environ.get("AEGIS_DB", str(DEFAULT_DB)))


class Store:
    def __init__(self, path=None):
        self.path = Path(path or db_path())
        if not self.path.exists() and self.path == DEFAULT_DB and OLD_DB.exists():
            # rename the pre-Aegis database in place so setups keep their state
            OLD_DB.rename(self.path)
            for suffix in ("-wal", "-shm"):
                stale = Path(str(OLD_DB) + suffix)
                if stale.exists():
                    stale.rename(Path(str(self.path) + suffix))
        self._lock = threading.Lock()

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self):
        with self._lock, self.connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute("pragma journal_mode=wal")
        return self

    # ------------------------------------------------------------------ settings
    def get(self, key: str, default: str | None = None) -> str:
        with self.connect() as conn:
            row = conn.execute("select value from settings where key=?", (key,)).fetchone()
        if row is None:
            value = SETTING_DEFAULTS.get(key, default)
            return "" if value is None else str(value)
        return row["value"]

    def get_many(self, keys):
        return {key: self.get(key) for key in keys}

    def set(self, key, value):
        with self._lock, self.connect() as conn:
            conn.execute(
                "insert into settings (key, value) values (?, ?) "
                "on conflict(key) do update set value=excluded.value",
                (key, str(value)),
            )

    def set_many(self, values):
        with self._lock, self.connect() as conn:
            conn.executemany(
                "insert into settings (key, value) values (?, ?) "
                "on conflict(key) do update set value=excluded.value",
                [(k, str(v)) for k, v in values.items()],
            )

    # --------------------------------------------------------------------- admin
    def admin_configured(self):
        return bool(self.get("admin_hash", ""))

    def set_admin_password(self, password):
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS)
        self.set("admin_hash", "%s$%s" % (salt, digest.hex()))
        if not self.get("session_secret", ""):
            self.set("session_secret", secrets.token_hex(32))

    def check_admin_password(self, password):
        stored = self.get("admin_hash", "")
        if not stored:
            return False
        salt, _, digest = stored.partition("$")
        if not salt or not digest:
            return False
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS)
        return hmac.compare_digest(candidate.hex(), digest)

    # ------------------------------------------------------------------- logging
    def log_guard(self, verdict, meta=None):
        meta = meta or {}
        with self._lock, self.connect() as conn:
            conn.execute(
                "insert into guard_decisions (ts, endpoint, label, decision, mode, threshold, model,"
                " latency_ms, note, probabilities, triggers, text_sha256, excerpt)"
                " values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    time.time(),
                    meta.get("endpoint"),
                    verdict.get("label"),
                    verdict.get("decision"),
                    verdict.get("mode"),
                    verdict.get("threshold"),
                    verdict.get("model"),
                    verdict.get("latency_ms"),
                    verdict.get("note"),
                    json.dumps(verdict.get("probabilities") or {}),
                    json.dumps(verdict.get("triggers") or []),
                    verdict.get("text_sha256"),
                    verdict.get("excerpt"),
                ),
            )

    def log_abuse(self, verdict, meta=None):
        meta = meta or {}
        with self._lock, self.connect() as conn:
            conn.execute(
                "insert into abuse_decisions (ts, endpoint, client, label, decision, mode, threshold,"
                " abuse_score, severity, category, pattern, recommended_action, model, latency_ms,"
                " triggers, narrative, error)"
                " values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    time.time(),
                    meta.get("endpoint"),
                    verdict.get("client"),
                    verdict.get("label"),
                    verdict.get("decision"),
                    verdict.get("mode"),
                    verdict.get("threshold"),
                    verdict.get("abuse_score"),
                    verdict.get("severity"),
                    verdict.get("category"),
                    verdict.get("pattern"),
                    verdict.get("recommended_action"),
                    verdict.get("model"),
                    verdict.get("latency_ms"),
                    json.dumps(verdict.get("triggered_by") or []),
                    verdict.get("narrative"),
                    verdict.get("error"),
                ),
            )

    def log_block(self, client, action, category="", score=0.0, until=0.0):
        with self._lock, self.connect() as conn:
            conn.execute(
                "insert into block_events (ts, client, action, category, score, until) values (?, ?, ?, ?, ?, ?)",
                (time.time(), client, action, category, score, until),
            )

    # ------------------------------------------------------------------- queries
    def _counts(self, conn, since):
        guard = conn.execute(
            "select count(*) as total, coalesce(sum(label='block'), 0) as blocked"
            " from guard_decisions where ts>=?", (since,)).fetchone()
        abuse = conn.execute(
            "select count(*) as total, coalesce(sum(label='block'), 0) as blocked,"
            " coalesce(sum(label='would-block'), 0) as flagged"
            " from abuse_decisions where ts>=?", (since,)).fetchone()
        return guard, abuse

    def overview(self, retention_days, blocked_now):
        now = time.time()
        since = now - retention_days * 86400
        day_ago = now - 86400
        with self.connect() as conn:
            total_guard, total_abuse = self._counts(conn, since)
            day_guard, day_abuse = self._counts(conn, day_ago)
            blocks_24h = conn.execute(
                "select count(*) from block_events where action='block' and ts>=?", (day_ago,)).fetchone()[0]

            series = []
            buckets = {}
            start = int(now // 3600) * 3600 - 23 * 3600
            for row in conn.execute(
                    "select cast(ts/3600 as int)*3600 as h, count(*) as total,"
                    " coalesce(sum(label='block'), 0) as blocked"
                    " from guard_decisions where ts>=? group by h", (start,)):
                buckets.setdefault(row["h"], {})["guard_total"] = row["total"]
                buckets[row["h"]]["guard_blocked"] = row["blocked"]
            for row in conn.execute(
                    "select cast(ts/3600 as int)*3600 as h, count(*) as total,"
                    " coalesce(sum(label='block'), 0) as blocked"
                    " from abuse_decisions where ts>=? group by h", (start,)):
                buckets.setdefault(row["h"], {})["abuse_total"] = row["total"]
                buckets[row["h"]]["abuse_blocked"] = row["blocked"]
            for i in range(24):
                h = start + i * 3600
                b = buckets.get(h, {})
                series.append({
                    "ts": h,
                    "guard_total": b.get("guard_total", 0),
                    "guard_blocked": b.get("guard_blocked", 0),
                    "abuse_total": b.get("abuse_total", 0),
                    "abuse_blocked": b.get("abuse_blocked", 0),
                })

            minute_series = []
            minute_buckets = {}
            mstart = int(now // 60) * 60 - 59 * 60
            for row in conn.execute(
                    "select cast(ts/60 as int)*60 as m, count(*) as total,"
                    " coalesce(sum(label='block'), 0) as blocked"
                    " from guard_decisions where ts>=? group by m", (mstart,)):
                minute_buckets.setdefault(row["m"], {})["guard_total"] = row["total"]
                minute_buckets[row["m"]]["guard_blocked"] = row["blocked"]
            for row in conn.execute(
                    "select cast(ts/60 as int)*60 as m, count(*) as total,"
                    " coalesce(sum(label='block'), 0) as blocked"
                    " from abuse_decisions where ts>=? group by m", (mstart,)):
                minute_buckets.setdefault(row["m"], {})["abuse_total"] = row["total"]
                minute_buckets[row["m"]]["abuse_blocked"] = row["blocked"]
            for i in range(60):
                m = mstart + i * 60
                b = minute_buckets.get(m, {})
                minute_series.append({
                    "ts": m,
                    "guard_total": b.get("guard_total", 0),
                    "guard_blocked": b.get("guard_blocked", 0),
                    "abuse_total": b.get("abuse_total", 0),
                    "abuse_blocked": b.get("abuse_blocked", 0),
                })

            categories = [dict(row) for row in conn.execute(
                "select category, count(*) as n from abuse_decisions where ts>=? and category is not null"
                " group by category order by n desc limit 6", (since,))]

            recent_guard = [dict(row) for row in conn.execute(
                "select ts, label, note, model, latency_ms, text_sha256 from guard_decisions"
                " order by ts desc limit 8")]
            recent_abuse = [dict(row) for row in conn.execute(
                "select ts, client, label, category, abuse_score from abuse_decisions"
                " order by ts desc limit 8")]

        for entry in recent_guard:
            entry["type"] = "guard"
        for entry in recent_abuse:
            entry["type"] = "abuse"
        recent = sorted(recent_guard + recent_abuse, key=lambda e: e["ts"], reverse=True)[:10]

        return {
            "retention_days": retention_days,
            "totals": {
                "guard_total": total_guard["total"], "guard_blocked": total_guard["blocked"],
                "abuse_total": total_abuse["total"], "abuse_blocked": total_abuse["blocked"],
                "abuse_flagged": total_abuse["flagged"],
            },
            "last_24h": {
                "guard_total": day_guard["total"], "guard_blocked": day_guard["blocked"],
                "abuse_total": day_abuse["total"], "abuse_blocked": day_abuse["blocked"],
                "blocks": blocks_24h,
            },
            "blocked_now": blocked_now,
            "series": series,
            "minute_series": minute_series,
            "categories": categories,
            "recent": recent,
        }

    def decisions(self, kind, limit=50, label=None):
        table = "abuse_decisions" if kind == "abuse" else "guard_decisions"
        sql = "select * from %s" % table
        params = []
        if label:
            sql += " where label=?"
            params.append(label)
        sql += " order by ts desc limit ?"
        params.append(max(1, min(int(limit), 500)))
        with self.connect() as conn:
            rows = [dict(row) for row in conn.execute(sql, params)]
        for row in rows:
            try:
                row["probabilities"] = json.loads(row.get("probabilities") or "{}")
            except ValueError:
                row["probabilities"] = {}
            try:
                row["triggers"] = json.loads(row.get("triggers") or "[]")
            except ValueError:
                row["triggers"] = []
        return rows

    def list_rules(self):
        with self.connect() as conn:
            rows = conn.execute(
                "select device, reason, created_at, expires_at from device_rules"
                " order by created_at desc").fetchall()
        return [dict(row) for row in rows]

    def block_events(self, limit=200):
        with self.connect() as conn:
            rows = conn.execute(
                "select ts, client, action, category, score, until from block_events"
                " order by ts desc limit ?", (max(1, int(limit)),)).fetchall()
        return [dict(row) for row in rows]

    def log_agent(self, mode, source, action, target, reason, detail=""):
        with self._lock, self.connect() as conn:
            conn.execute(
                "insert into agent_events (ts, mode, source, action, target, reason, detail)"
                " values (?, ?, ?, ?, ?, ?, ?)",
                (time.time(), mode, source, action, target, reason, detail),
            )

    def agent_events(self, limit=100):
        with self.connect() as conn:
            rows = conn.execute(
                "select ts, mode, source, action, target, reason, detail from agent_events"
                " order by ts desc limit ?", (max(1, min(int(limit), 500)),)).fetchall()
        return [dict(row) for row in rows]

    def add_rule(self, device, reason="", ttl=0.0):
        expires = time.time() + float(ttl) if float(ttl or 0) > 0 else None
        with self._lock, self.connect() as conn:
            conn.execute(
                "insert into device_rules (device, reason, created_at, expires_at)"
                " values (?, ?, ?, ?)"
                " on conflict(device) do update set reason=excluded.reason,"
                " created_at=excluded.created_at, expires_at=excluded.expires_at",
                (device, reason, time.time(), expires),
            )
        return {"device": device, "reason": reason, "expires_at": expires}

    def remove_rule(self, device):
        with self._lock, self.connect() as conn:
            cur = conn.execute("delete from device_rules where device=?", (device,))
        return cur.rowcount > 0

    def purge(self, retention_days):
        cutoff = time.time() - retention_days * 86400
        removed = {}
        with self._lock, self.connect() as conn:
            for table in ("guard_decisions", "abuse_decisions", "block_events", "agent_events"):
                cur = conn.execute("delete from %s where ts<?" % table, (cutoff,))
                removed[table] = cur.rowcount
            cur = conn.execute(
                "delete from device_rules where expires_at is not null and expires_at<?",
                (time.time(),))
            removed["device_rules"] = cur.rowcount
        return removed


store = Store().init()
