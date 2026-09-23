#!/usr/bin/env python3
"""Fire 300 concurrent GET requests at http://127.0.0.1:8978/demo-api.

Pure asyncio + aiohttp, one event loop, all 300 requests in flight at once
(TCPConnector limit == CONCURRENCY, so every task opens its own connection).

Reports the exact destination of every shot: request URL, the peer the TCP
connection actually landed on (host:port), plus status counts, wall time,
throughput and latency stats. Set SHOW_EVERY_REQUEST = True to print all
300 per-request lines instead of a sample.
"""

import asyncio
import time
from collections import Counter

import aiohttp

URL = "http://127.0.0.1:8978/demo-api"
REQUESTS = 300
TIMEOUT = 15          # seconds per request
SAMPLE = 5            # per-request lines to print when not showing all
SHOW_EVERY_REQUEST = False


def peer_of(resp: aiohttp.ClientResponse) -> str | None:
    """Resolved socket the request was sent to: 'host:port'."""
    conn = resp.connection
    if conn is None or conn.transport is None:
        return None
    peer = conn.transport.get_extra_info("peername")
    return f"{peer[0]}:{peer[1]}" if peer else None


async def one_request(session: aiohttp.ClientSession, idx: int, results: list) -> None:
    t0 = time.perf_counter()
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
                    "latency": time.perf_counter() - t0,
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
                "latency": time.perf_counter() - t0,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )


async def main() -> None:
    results: list = []
    connector = aiohttp.TCPConnector(limit=REQUESTS)  # all requests concurrent
    timeout = aiohttp.ClientTimeout(total=TIMEOUT)

    print(f"target URL         : {URL}")
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        wall_start = time.perf_counter()
        await asyncio.gather(*(one_request(session, i, results) for i in range(REQUESTS)))
        wall = time.perf_counter() - wall_start

    ok = [r for r in results if r["error"] is None]
    errs = Counter(r["error"] for r in results if r["error"])
    statuses = Counter(r["status"] for r in ok)
    peers = Counter(r["peer"] or "-" for r in results)
    lat = sorted(r["latency"] for r in ok)

    print(f"requests sent      : {len(results)}")
    print(f"completed (no err) : {len(ok)}")
    print(f"failed             : {len(results) - len(ok)}")
    print(f"wall time          : {wall:.3f}s")
    print(f"throughput         : {len(results) / wall:.1f} req/s")
    print(f"status breakdown   : {dict(statuses)}")
    print("destinations       : " + ", ".join(f"{p} ({n} req)" for p, n in peers.items()))
    if lat:
        print(
            f"latency min/avg/max: {lat[0] * 1000:.1f} / "
            f"{sum(lat) / len(lat) * 1000:.1f} / {lat[-1] * 1000:.1f} ms"
        )
    if errs:
        print("errors:")
        for msg, n in errs.items():
            print(f"  {n}x {msg}")

    shown = len(results) if SHOW_EVERY_REQUEST else min(SAMPLE, len(results))
    print(f"per-request trace  (first {shown} of {len(results)}):")
    for r in results[:shown]:
        outcome = r["status"] if r["status"] is not None else r["error"]
        print(
            f"  #{r['idx']:03d}  {r['method']} {r['url']} -> {r['peer'] or '-'} "
            f"-> {outcome}  ({r['latency'] * 1000:.1f} ms, {r['bytes']} B)"
        )


if __name__ == "__main__":
    asyncio.run(main())
