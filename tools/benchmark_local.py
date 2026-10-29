"""Disposable current-schema benchmark. No network and no production database."""

import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apps.proxypool.service.cache import CandidateCache
from apps.proxypool.service.config import Settings
from apps.proxypool.service.database import Database
from apps.proxypool.service.writer import Writer


def measure(function, count=5):
    function()
    values = []
    for _ in range(count):
        start = time.perf_counter()
        function()
        values.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(values), 3)


def benchmark(proxies, targets):
    if not 1 <= proxies <= 1000000 or not 1 <= targets <= 32 or proxies * targets > 5000000:
        raise ValueError("Limit: 1M proxies, 32 targets, 5M quality rows")
    base = ROOT / ".local/benchmarks"
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="run-", dir=base) as folder:
        folder = Path(folder).resolve()
        assert folder.is_relative_to(base.resolve())
        config = replace(
            Settings(),
            database=str(folder / "bench.sqlite"),
            write_flush_seconds=0.001,
            pool_limit=max(1, proxies - min(1000, max(1, proxies // 10))),
        )
        database = Database(config)
        connection = database.initialize()
        try:
            connection.execute("BEGIN")
            connection.executemany(
                "INSERT INTO proxies(scheme,host,port) VALUES(?,?,?)",
                (
                    (
                        "http",
                        f"198.{18 + (i // 65536) % 2}.{(i // 256) % 256}.{i % 256}",
                        10000 + i // 131072,
                    )
                    for i in range(proxies)
                ),
            )
            connection.executemany(
                "INSERT INTO targets(name,test_url,enabled) VALUES(?,?,1)",
                ((f"target{i}", "https://example.invalid/" + str(i)) for i in range(targets)),
            )
            now = int(time.time() * 1000) - 1000000
            connection.executemany(
                """INSERT INTO proxy_quality(target_id,proxy_id,target_revision,
                 result_bits,sample_count,feedback_count,score,successful_latency_ewma_ms,last_feedback_at)
                 VALUES(?,?,1,?,63,100,?,?,?)""",
                (
                    (
                        target,
                        proxy,
                        (1 << (proxy % 64)) - 1,
                        float(50 + (proxy % 4000) / 100),
                        float(30 + proxy % 900) + 0.125,
                        now,
                    )
                    for target in range(1, targets + 1)
                    for proxy in range(1, proxies + 1)
                ),
            )
            connection.execute("COMMIT")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            connection.close()
        writer = Writer(database)
        try:
            measurements = {
                "select_100_with_endpoint_left_join_ms": measure(
                    lambda: database.select_tests(1, int(time.time() * 1000), 100)
                ),
                "rank_100_with_endpoint_join_ms": measure(lambda: database.candidates(1, 100, 50)),
                "stats_all_targets_ms": measure(database.stats),
                "eviction_group_plan_ms": measure(
                    lambda: writer.submit("plan_eviction", None, urgent=True).result()
                ),
            }
            size = min(1000, proxies)

            def update():
                records = [
                    {
                        "result_id": uuid4().hex,
                        "proxy_id": i + 1,
                        "target_id": 1,
                        "target_revision": 1,
                        "outcome": "response",
                        "http_status": 200,
                        "latency_ms": 100.0,
                    }
                    for i in range(size)
                ]
                writer.submit("feedback", records, urgent=True).result()

            measurements[f"feedback_{size}_with_commit_ms"] = measure(update)

            async def acquire():
                cache = CandidateCache(config)
                for _ in range(1000):
                    await cache.acquire(
                        {"target_id": 1, "revision": 1, "enabled": True},
                        lambda *args: asyncio.to_thread(database.candidates, *args),
                    )

            measurements["acquire_1000_including_cache_refills_ms"] = measure(
                lambda: asyncio.run(acquire()), 3
            )
            with database.reader() as reader:
                delete_plan = [
                    tuple(x)
                    for x in reader.execute(
                        "EXPLAIN QUERY PLAN DELETE FROM proxies WHERE proxy_id=1"
                    )
                ]
            ids = list(range(1, min(1000, proxies) + 1))
            start = time.perf_counter()
            deleted = writer.submit("delete_proxies", ids, urgent=True).result()
            measurements["delete_up_to_1000_with_cascades_commit_ms"] = round(
                (time.perf_counter() - start) * 1000, 3
            )
        finally:
            writer.close()
        report = {
            "proxies": proxies,
            "targets": targets,
            "quality_rows": proxies * targets,
            "python": platform.python_version(),
            "sqlite": __import__("sqlite3").sqlite_version,
            "scope": "warm local business-layer operations, no HTTP, no Docker, not server throughput",
            "measurements": measurements,
            "delete_query_plan": delete_plan,
            "database_bytes": Path(config.database).stat().st_size,
            "deleted": deleted["deleted"],
        }
    report["temporary_database_removed"] = not folder.exists()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxies", type=int, default=10000)
    parser.add_argument("--targets", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.proxies, args.targets), indent=2))


if __name__ == "__main__":
    main()
