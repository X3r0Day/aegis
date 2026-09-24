#!/usr/bin/env python3
"""Print a clean summary of the Aegis SQLite database for demos.

Opens the database read-only (safe while the guard is running) and shows:
table counts, the latest guard and abuse decisions, block events, device
rules, agent actions, and the stored settings (secrets masked).

    .venv/bin/python demo-scripts/show_db.py
    .venv/bin/python demo-scripts/show_db.py --limit 5 --db guard/data/aegis.db
"""

import argparse
import sqlite3
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE.parent / "guard" / "data" / "aegis.db"

TABLES = ("settings", "guard_decisions", "abuse_decisions", "block_events",
          "device_rules", "agent_events")
SECRET_WORDS = ("key", "hash", "secret", "salt")


def fmt_ts(value):
    try:
        return datetime.fromtimestamp(float(value)).strftime("%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return str(value)


def clip(value, width):
    text = "" if value is None else str(value)
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: width - 3] + "..."


def table_rows(conn, name, limit, order_by="rowid"):
    try:
        rows = conn.execute(
            "select * from %s order by %s desc limit ?" % (name, order_by),
            (limit,)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(row) for row in rows]


def print_table(header, columns, rows):
    print(header)
    if not rows:
        print("  (none)")
        print()
        return
    widths = [max(len(col), max(len(clip(row[col], 60)) for row in rows)) for col in columns]
    line = "  " + "  ".join(col.ljust(w) for col, w in zip(columns, widths))
    print(line)
    print("  " + "  ".join("-" * w for w in widths))
    for row in rows:
        print("  " + "  ".join(clip(row[col], w).ljust(w) for col, w in zip(columns, widths)))
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()

    path = Path(args.db)
    if not path.exists():
        raise SystemExit("database not found: %s" % path)

    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    conn.row_factory = sqlite3.Row

    size_kb = max(1, round(path.stat().st_size / 1024))
    print("Aegis database    : %s (%d KB)" % (path, size_kb))
    print("read at           : %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print()

    print("table counts")
    for name in TABLES:
        try:
            count = conn.execute("select count(*) from %s" % name).fetchone()[0]
            print("  %-18s %d" % (name, count))
        except sqlite3.OperationalError:
            print("  %-18s missing" % name)
    print()

    guard = table_rows(conn, "guard_decisions", args.limit, order_by="ts")
    for row in guard:
        row["when"] = fmt_ts(row["ts"])
    print_table("last guard decisions", ["when", "label", "note", "model", "latency_ms"], guard)

    abuse = table_rows(conn, "abuse_decisions", args.limit, order_by="ts")
    for row in abuse:
        row["when"] = fmt_ts(row["ts"])
    print_table("last abuse verdicts",
                ["when", "client", "label", "category", "abuse_score", "severity"], abuse)

    blocks = table_rows(conn, "block_events", args.limit, order_by="ts")
    for row in blocks:
        row["when"] = fmt_ts(row["ts"])
        row["until"] = "expired" if row["until"] and row["until"] < time.time() else "active"
    print_table("last block events", ["when", "client", "action", "category", "until"], blocks)

    rules = table_rows(conn, "device_rules", args.limit, order_by="created_at")
    for row in rules:
        row["added"] = fmt_ts(row["created_at"])
        row["expires"] = "permanent" if not row["expires_at"] else fmt_ts(row["expires_at"])
    print_table("device blacklist rules", ["device", "reason", "added", "expires"], rules)

    events = table_rows(conn, "agent_events", args.limit)
    for row in events:
        row["when"] = fmt_ts(row["ts"])
    print_table("last agent actions", ["when", "mode", "source", "action", "target", "reason"], events)

    try:
        settings = conn.execute("select key, value from settings order by key").fetchall()
    except sqlite3.OperationalError:
        settings = []
    print("settings")
    if not settings:
        print("  (none)")
    for row in settings:
        value = row["value"] or ""
        if any(word in row["key"] for word in SECRET_WORDS):
            value = (value[:8] + "...") if value else "(not set)"
        print("  %-34s %s" % (row["key"], value))

    conn.close()


if __name__ == "__main__":
    main()
