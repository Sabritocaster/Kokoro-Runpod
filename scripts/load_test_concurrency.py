#!/usr/bin/env python3
import argparse
import asyncio
import csv
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import aiohttp
except Exception:
    print("ERROR: missing dependency 'aiohttp'. Install with: pip install aiohttp", file=sys.stderr)
    raise


@dataclass
class RequestResult:
    level: int
    req_id: int
    status: int
    ok: bool
    total_s: float
    ttfb_s: float | None
    resp_bytes: int
    processing_s: float | None
    chunk_count: int | None
    error: str


@dataclass
class PingResult:
    status: int
    total_s: float
    ttfb_s: float | None
    resp_bytes: int
    error: str


def percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * p
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = rank - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


async def send_one(
    session: aiohttp.ClientSession,
    level: int,
    req_id: int,
    base_url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_s: float,
) -> RequestResult:
    url = f"{base_url.rstrip('/')}/tts"
    start = time.perf_counter()
    ttfb_s: float | None = None

    try:
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with session.post(url, headers=headers, json=payload, timeout=timeout) as resp:
            status = resp.status

            first_chunk = await resp.content.readany()
            if ttfb_s is None:
                ttfb_s = time.perf_counter() - start
            size = len(first_chunk)

            body_rest = await resp.read()
            size += len(body_rest)

            total_s = time.perf_counter() - start
            ok = status == 200
            error = "" if ok else f"http_{status}"
            processing_raw = resp.headers.get("X-Processing-Time", "").strip()
            chunk_raw = resp.headers.get("X-Chunk-Count", "").strip()
            try:
                processing_s = float(processing_raw) if processing_raw else None
            except ValueError:
                processing_s = None
            try:
                chunk_count = int(chunk_raw) if chunk_raw else None
            except ValueError:
                chunk_count = None
            return RequestResult(
                level=level,
                req_id=req_id,
                status=status,
                ok=ok,
                total_s=total_s,
                ttfb_s=ttfb_s,
                resp_bytes=size,
                processing_s=processing_s,
                chunk_count=chunk_count,
                error=error,
            )
    except asyncio.TimeoutError:
        total_s = time.perf_counter() - start
        return RequestResult(level, req_id, 0, False, total_s, ttfb_s, 0, None, None, "timeout")
    except Exception as exc:
        total_s = time.perf_counter() - start
        return RequestResult(level, req_id, 0, False, total_s, ttfb_s, 0, None, None, str(exc))


async def run_level(
    level: int,
    requests_per_level: int,
    base_url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_s: float,
) -> tuple[list[RequestResult], dict[str, Any]]:
    connector = aiohttp.TCPConnector(limit=0, ssl=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            asyncio.create_task(
                send_one(
                    session=session,
                    level=level,
                    req_id=i,
                    base_url=base_url,
                    headers=headers,
                    payload=payload,
                    timeout_s=timeout_s,
                )
            )
            for i in range(1, requests_per_level + 1)
        ]

        wall_start = time.perf_counter()
        results = await asyncio.gather(*tasks)
        wall_s = time.perf_counter() - wall_start

    oks = [r for r in results if r.ok]
    fails = [r for r in results if not r.ok]
    totals = sorted(r.total_s for r in results)
    ttfbs = sorted(r.ttfb_s for r in results if r.ttfb_s is not None)
    processing_times = sorted(r.processing_s for r in results if r.processing_s is not None)

    status_counts: dict[str, int] = {}
    for r in results:
        key = str(r.status)
        status_counts[key] = status_counts.get(key, 0) + 1

    summary = {
        "concurrency": level,
        "requests": requests_per_level,
        "ok": len(oks),
        "failed": len(fails),
        "success_rate": (len(oks) / len(results) * 100.0) if results else 0.0,
        "wall_s": wall_s,
        "rps": (len(results) / wall_s) if wall_s > 0 else 0.0,
        "latency_avg_s": statistics.mean(totals) if totals else 0.0,
        "latency_p50_s": percentile(totals, 0.50),
        "latency_p90_s": percentile(totals, 0.90),
        "latency_p95_s": percentile(totals, 0.95),
        "latency_p99_s": percentile(totals, 0.99),
        "latency_max_s": max(totals) if totals else 0.0,
        "ttfb_avg_s": statistics.mean(ttfbs) if ttfbs else 0.0,
        "processing_avg_s": statistics.mean(processing_times) if processing_times else 0.0,
        "processing_p95_s": percentile(processing_times, 0.95),
        "status_counts": status_counts,
    }
    return results, summary


async def run_ping(base_url: str, headers: dict[str, str], timeout_s: float) -> PingResult:
    url = f"{base_url.rstrip('/')}/ping"
    connector = aiohttp.TCPConnector(limit=1, ssl=True)
    start = time.perf_counter()
    ttfb_s: float | None = None

    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            timeout = aiohttp.ClientTimeout(total=timeout_s)
            async with session.get(url, headers=headers, timeout=timeout) as resp:
                status = resp.status
                first_chunk = await resp.content.readany()
                if ttfb_s is None:
                    ttfb_s = time.perf_counter() - start
                size = len(first_chunk)
                rest = await resp.read()
                size += len(rest)
                total_s = time.perf_counter() - start
                return PingResult(status=status, total_s=total_s, ttfb_s=ttfb_s, resp_bytes=size, error="")
        except asyncio.TimeoutError:
            total_s = time.perf_counter() - start
            return PingResult(status=0, total_s=total_s, ttfb_s=ttfb_s, resp_bytes=0, error="timeout")
        except Exception as exc:
            total_s = time.perf_counter() - start
            return PingResult(status=0, total_s=total_s, ttfb_s=ttfb_s, resp_bytes=0, error=str(exc))


def print_summary_row(s: dict[str, Any]) -> None:
    print(
        "concurrency={c:>4} requests={r:>4} ok={ok:>4} fail={f:>4} "
        "success={sr:>6.2f}% rps={rps:>8.2f} p95={p95:>7.3f}s p99={p99:>7.3f}s max={mx:>7.3f}s".format(
            c=s["concurrency"],
            r=s["requests"],
            ok=s["ok"],
            f=s["failed"],
            sr=s["success_rate"],
            rps=s["rps"],
            p95=s["latency_p95_s"],
            p99=s["latency_p99_s"],
            mx=s["latency_max_s"],
        )
    )


def print_final_summary(base_url: str, ping: PingResult, summaries: list[dict[str, Any]]) -> None:
    print("\n=== Final Summary ===")
    print(
        "base_url={base} ping_status={status} ping_total={total:.3f}s ping_ttfb={ttfb:.3f}s".format(
            base=base_url,
            status=ping.status,
            total=ping.total_s,
            ttfb=ping.ttfb_s or 0.0,
        )
    )
    print(
        "lvl req ok fail succ% rps lat_avg p95 p99 max ttfb_avg proc_avg proc_p95\n"
        "--- --- -- ---- ----- --- ------- --- --- --- -------- -------- --------"
    )
    for s in summaries:
        print(
            "{lvl:>3} {req:>3} {ok:>2} {fail:>4} {succ:>5.1f} {rps:>3.1f} {avg:>7.3f} {p95:>3.3f} {p99:>3.3f} {mx:>3.3f} {ttfb:>8.3f} {pavg:>8.3f} {pp95:>8.3f}".format(
                lvl=s["concurrency"],
                req=s["requests"],
                ok=s["ok"],
                fail=s["failed"],
                succ=s["success_rate"],
                rps=s["rps"],
                avg=s["latency_avg_s"],
                p95=s["latency_p95_s"],
                p99=s["latency_p99_s"],
                mx=s["latency_max_s"],
                ttfb=s["ttfb_avg_s"],
                pavg=s.get("processing_avg_s", 0.0),
                pp95=s.get("processing_p95_s", 0.0),
            )
        )


def ensure_auth(api_key: str) -> str:
    key = api_key.strip()
    if not key:
        raise ValueError("RUNPOD_API_KEY is empty")
    if key in {"rp_xxx", "rpa_xxx", "YOUR_API_KEY"}:
        raise ValueError("RUNPOD_API_KEY looks like a placeholder")
    return key


def load_dotenv() -> None:
    dotenv_candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ]
    for dotenv_path in dotenv_candidates:
        if not dotenv_path.exists():
            continue
        for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
        break


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "text": args.text,
        "voice": args.voice,
        "speed": args.speed,
        "format": "wav",
        "split_long_text": args.split_long_text,
        "max_chars_per_chunk": args.max_chars_per_chunk,
    }


def parse_args() -> argparse.Namespace:
    load_dotenv()
    p = argparse.ArgumentParser(description="Runpod /tts concurrency load test")
    p.add_argument("--base-url", required=True, help="Endpoint base URL, e.g. https://<id>.api.runpod.ai")
    p.add_argument(
        "--levels",
        default="1,10, 50,100",
        help="Comma-separated concurrency levels",
    )
    p.add_argument(
        "--requests-per-level",
        type=int,
        default=0,
        help="If 0, equals concurrency level. Otherwise fixed request count per level.",
    )
    p.add_argument("--timeout", type=float, default=180.0, help="Per-request timeout seconds")
    p.add_argument("--pause", type=float, default=2.0, help="Pause seconds between levels")
    p.add_argument("--text", default="Merhaba dunya, concurrency load test.")
    p.add_argument("--voice", default="default")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--split-long-text", action="store_true", default=True)
    p.add_argument("--max-chars-per-chunk", type=int, default=180)
    p.add_argument("--out-dir", default="results")
    p.add_argument("--api-key", default=os.getenv("RUNPOD_API_KEY", ""), help="Runpod API key; falls back to RUNPOD_API_KEY env var")
    return p.parse_args()


async def main_async() -> int:
    args = parse_args()
    api_key = ensure_auth(args.api_key)

    levels = [int(x.strip()) for x in args.levels.split(",") if x.strip()]
    if any(l <= 0 for l in levels):
        raise ValueError("All concurrency levels must be > 0")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    summary_path = out_dir / f"concurrency_summary_{ts}.json"
    requests_path = out_dir / f"concurrency_requests_{ts}.csv"
    levels_path = out_dir / f"concurrency_levels_{ts}.csv"
    ping_path = out_dir / f"concurrency_ping_{ts}.csv"

    payload = build_payload(args)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    all_results: list[RequestResult] = []
    all_summaries: list[dict[str, Any]] = []

    print(f"Base URL: {args.base_url}")
    print(f"Levels: {levels}")
    print(f"Requests per level: {'same as level' if args.requests_per_level == 0 else args.requests_per_level}")
    print()

    print("--- Running initial cold-start ping ---")
    ping = await run_ping(args.base_url, headers, args.timeout)
    ping_ok = ping.status in {200, 204}
    print(
        "ping status={status} total={total:.3f}s ttfb={ttfb:.3f}s bytes={size} err={err}".format(
            status=ping.status,
            total=ping.total_s,
            ttfb=ping.ttfb_s or 0.0,
            size=ping.resp_bytes,
            err=ping.error or "-",
        )
    )
    if ping.status == 401:
        raise ValueError("Initial /ping returned 401. Check RUNPOD_API_KEY.")

    for level in levels:
        req_count = level if args.requests_per_level == 0 else args.requests_per_level
        print(f"--- Running level concurrency={level}, requests={req_count} ---")

        results, summary = await run_level(
            level=level,
            requests_per_level=req_count,
            base_url=args.base_url,
            headers=headers,
            payload=payload,
            timeout_s=args.timeout,
        )
        all_results.extend(results)
        all_summaries.append(summary)
        print_summary_row(summary)

        if args.pause > 0:
            await asyncio.sleep(args.pause)

    with ping_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["status", "ok", "total_s", "ttfb_s", "resp_bytes", "error"])
        writer.writerow(
            [
                ping.status,
                int(ping_ok),
                f"{ping.total_s:.6f}",
                "" if ping.ttfb_s is None else f"{ping.ttfb_s:.6f}",
                ping.resp_bytes,
                ping.error,
            ]
        )

    with requests_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "level",
                "req_id",
                "status",
                "ok",
                "total_s",
                "ttfb_s",
                "processing_s",
                "chunk_count",
                "resp_bytes",
                "error",
            ]
        )
        for r in all_results:
            writer.writerow(
                [
                    r.level,
                    r.req_id,
                    r.status,
                    int(r.ok),
                    f"{r.total_s:.6f}",
                    "" if r.ttfb_s is None else f"{r.ttfb_s:.6f}",
                    "" if r.processing_s is None else f"{r.processing_s:.6f}",
                    "" if r.chunk_count is None else r.chunk_count,
                    r.resp_bytes,
                    r.error,
                ]
            )

    with levels_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "concurrency",
                "requests",
                "ok",
                "failed",
                "success_rate",
                "wall_s",
                "rps",
                "latency_avg_s",
                "latency_p50_s",
                "latency_p90_s",
                "latency_p95_s",
                "latency_p99_s",
                "latency_max_s",
                "ttfb_avg_s",
                "processing_avg_s",
                "processing_p95_s",
                "status_counts",
            ]
        )
        for s in all_summaries:
            writer.writerow(
                [
                    s["concurrency"],
                    s["requests"],
                    s["ok"],
                    s["failed"],
                    f"{s['success_rate']:.4f}",
                    f"{s['wall_s']:.6f}",
                    f"{s['rps']:.6f}",
                    f"{s['latency_avg_s']:.6f}",
                    f"{s['latency_p50_s']:.6f}",
                    f"{s['latency_p90_s']:.6f}",
                    f"{s['latency_p95_s']:.6f}",
                    f"{s['latency_p99_s']:.6f}",
                    f"{s['latency_max_s']:.6f}",
                    f"{s['ttfb_avg_s']:.6f}",
                    f"{s['processing_avg_s']:.6f}",
                    f"{s['processing_p95_s']:.6f}",
                    json.dumps(s["status_counts"], ensure_ascii=True),
                ]
            )

    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "base_url": args.base_url,
                "levels": levels,
                "requests_per_level": args.requests_per_level,
                "initial_ping": {
                    "status": ping.status,
                    "ok": ping_ok,
                    "total_s": ping.total_s,
                    "ttfb_s": ping.ttfb_s,
                    "resp_bytes": ping.resp_bytes,
                    "error": ping.error,
                },
                "payload": payload,
                "summaries": all_summaries,
                "artifacts": {
                    "ping_csv": str(ping_path),
                    "requests_csv": str(requests_path),
                    "levels_csv": str(levels_path),
                },
            },
            f,
            indent=2,
            ensure_ascii=True,
        )

    print("\nSaved artifacts:")
    print(f"- {ping_path}")
    print(f"- {requests_path}")
    print(f"- {levels_path}")
    print(f"- {summary_path}")
    print_final_summary(args.base_url, ping, all_summaries)
    return 0


def main() -> int:
    try:
        return asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
