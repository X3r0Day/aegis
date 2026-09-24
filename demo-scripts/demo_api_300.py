#!/usr/bin/env python3
"""Stream up to 900 GET requests at http://127.0.0.1:8978/demo-api.

Instead of firing everything at once, requests are paced at RATE_PER_S with
CONCURRENCY sockets in flight. The guard needs several seconds to score the
client and decide to block, so the stream is deliberately long enough to watch
it get cut off mid-run: early requests return 200, then the guard starts
answering 429 while the script is still sending.

Prints a live progress line every second, an end-of-run timeline, the moment
the guard started rejecting, and the exact requests where the stream switched
from served to blocked. Set SHOW_EVERY_REQUEST = True for all 900 lines.
"""

import asyncio
import time
from collections import Counter

import aiohttp

URL = "http://127.0.0.1:8978/demo-api"
REQUESTS = 900
CONCURRENCY = 60        # sockets in flight
RATE_PER_S = 50         # launch rate, keeps the stream running past the verdict
TIMEOUT = 15            # seconds per request
SAMPLE = 5              # per-request lines in the trace when not showing all
SHOW_EVERY_REQUEST = False


def peer_of(resp: aiohttp.ClientResponse) -> str | None:
    """Resolved socket the request was sent to: 'host:port'."""
    conn = resp.connection
    if conn is None or conn.transport is None:
        return None
    peer = conn.transport.get_extra_info("peername")
    return f"{peer[0]}:{peer[1]}" if peer else None


async def one_request(session: aiohttp.ClientSession, idx: int, results: list, t0: float) -> None:
    started = time.perf_counter()
    try:
        async with session.get(URL) as resp:
            body = await resp.read()
            results.append(
                {
                    "idx": idx,
                    "method": "GET",
                    "url": str(resp.url),
                    "peer": peer_of(resp),
                    "status": resp.status,
                    "bytes": len(body),
                    "detail": (body[:120].decode("utf-8", "replace") if resp.status != 200 else ""),
                    "started": started - t0,
                    "finished": time.perf_counter() - t0,
                    "latency": time.perf_counter() - started,
                    "error": None,
                }
            )
    except Exception as exc:  # noqa: BLE001 - report anything the target throws
        results.append(
            {
                "idx": idx,
                "method": "GET",
                "url": URL,
                "peer": None,
                "status": None,
                "bytes": 0,
                "detail": "",
                "started": started - t0,
                "finished": time.perf_counter() - t0,
                "latency": time.perf_counter() - started,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )


async def progress(results: list, t0: float, stop: asyncio.Event) -> None:
    """Live line every second so the cutoff is visible while it happens."""
    while not stop.is_set():
        await asyncio.sleep(1.0)
        done = len(results)
        ok = sum(1 for r in results if r["status"] == 200)
        blocked = sum(1 for r in results if r["status"] == 429)
        other = done - ok - blocked
        line = f"  t={time.perf_counter() - t0:5.1f}s  done={done:3d}/{REQUESTS}  served={ok:3d}  blocked={blocked:3d}"
        if other:
            line += f"  other={other}"
        if blocked and ok:
            line += "  <- guard is rejecting the tail"
        print(line, flush=True)


async def main() -> None:
    results: list = []
    connector = aiohttp.TCPConnector(limit=CONCURRENCY)
    timeout = aiohttp.ClientTimeout(total=TIMEOUT)

    print("target URL         : " + URL)
    print(f"plan               : {REQUESTS} requests, {CONCURRENCY} in flight, {RATE_PER_S} launches/s")

    t0 = time.perf_counter()
    stop = asyncio.Event()
    monitor = asyncio.create_task(progress(results, t0, stop))

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        tasks = []
        next_launch = t0
        for i in range(REQUESTS):
            now = time.perf_counter()
            if next_launch > now:
                await asyncio.sleep(next_launch - now)
            next_launch += 1 / RATE_PER_S
            tasks.append(asyncio.create_task(one_request(session, i, results, t0)))
        await asyncio.gather(*tasks)
        wall = time.perf_counter() - t0

    stop.set()
    await monitor

    ok = [r for r in results if r["status"] == 200]
    blocked = [r for r in results if r["status"] == 429]
    errs = Counter(r["error"] for r in results if r["error"])
    statuses = Counter(r["status"] for r in results if r["error"] is None)
    peers = Counter(r["peer"] or "-" for r in results)
    lat = sorted(r["latency"] for r in ok)

    print()
    print(f"requests sent      : {len(results)}")
    print(f"served (200)       : {len(ok)}")
    print(f"blocked (429)      : {len(blocked)}")
    print(f"failed             : {len(results) - len(ok) - len(blocked)}")
    print(f"wall time          : {wall:.3f}s (stream would run {REQUESTS / RATE_PER_S:.1f}s unpaced by a block)")
    print(f"launch rate        : {RATE_PER_S}/s")
    print(f"status breakdown   : {dict(statuses)}")
    print("destinations       : " + ", ".join(f"{p} ({n} req)" for p, n in peers.items()))
    if lat:
        print(
            f"latency min/avg/max: {lat[0] * 1000:.1f} / "
            f"{sum(lat) / len(lat) * 1000:.1f} / {lat[-1] * 1000:.1f} ms"
        )

    if blocked and ok:
        last_ok = max(ok, key=lambda r: r["finished"])
        first_block = min(blocked, key=lambda r: r["finished"])
        print()
        print(f"cutoff             : last 200 at t={last_ok['finished']:.2f}s (#{last_ok['idx']}), "
              f"first 429 at t={first_block['finished']:.2f}s (#{first_block['idx']})")
        print(f"                     {len(blocked)} of the {REQUESTS} planned requests were rejected in flight")
        if first_block["detail"]:
            print(f"block response     : {first_block['detail']}")
    elif blocked:
        print("cutoff             : only 429s, the client was already blocked before the run")
    else:
        print("cutoff             : no 429s, the guard did not block during this run")

    timeline: dict[int, Counter] = {}
    for r in results:
        bucket = timeline.setdefault(int(r["finished"]), Counter())
        if r["status"] == 200:
            bucket["served"] += 1
        elif r["status"] == 429:
            bucket["blocked"] += 1
        elif r["error"]:
            bucket["failed"] += 1
    if timeline:
        print()
        print("timeline           : t  served  blocked  failed")
        for second in sorted(timeline):
            row = timeline[second]
            print(f"                     {second:2d}  {row['served']:6d}  {row['blocked']:7d}  {row['failed']:6d}")

    if errs:
        print("errors:")
        for msg, n in errs.items():
            print(f"  {n}x {msg}")

    print()
    if SHOW_EVERY_REQUEST:
        trace = sorted(results, key=lambda r: r["idx"])
    elif blocked and ok:
        first_block = min(blocked, key=lambda r: r["finished"])
        trace = results[:SAMPLE] + [r for r in results if r["idx"] in range(max(0, first_block["idx"] - 2), first_block["idx"] + 3)]
    else:
        trace = results[: min(SAMPLE, len(results))]
    print(f"per-request trace  ({len(trace)} of {len(results)}):")
    for r in trace:
        outcome = r["status"] if r["status"] is not None else r["error"]
        print(
            f"  #{r['idx']:03d}  {r['method']} {r['url']} -> {r['peer'] or '-'} "
            f"-> {outcome}  (t={r['finished']:5.2f}s, {r['latency'] * 1000:.1f} ms, {r['bytes']} B)"
        )


if __name__ == "__main__":
    asyncio.run(main())
