"""Current SQLite schema. Legacy PoC databases are deliberately not migrated."""

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
from .policy import needs_test, proxy_url, wilson

APPLICATION_ID = 1347964215
SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE proxies (
 proxy_id INTEGER PRIMARY KEY AUTOINCREMENT,
 scheme TEXT NOT NULL, host TEXT NOT NULL, port INTEGER NOT NULL,
 UNIQUE(scheme,host,port)
);
CREATE TABLE targets (
 target_id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL, test_url TEXT NOT NULL,
 enabled INTEGER NOT NULL, revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE proxy_quality (
 target_id INTEGER NOT NULL REFERENCES targets(target_id) ON DELETE CASCADE,
 proxy_id INTEGER NOT NULL REFERENCES proxies(proxy_id) ON DELETE CASCADE,
 target_revision INTEGER NOT NULL,
 result_bits INTEGER NOT NULL DEFAULT 0 CHECK(result_bits>=0),
 sample_count INTEGER NOT NULL DEFAULT 0 CHECK(sample_count BETWEEN 0 AND 63),
 feedback_count INTEGER NOT NULL DEFAULT 0,
 score REAL, successful_latency_ewma_ms REAL,
 last_feedback_at INTEGER, last_test_scheduled_at INTEGER,
 PRIMARY KEY(target_id,proxy_id)
) WITHOUT ROWID;
CREATE INDEX quality_rank ON proxy_quality(target_id,score DESC,successful_latency_ewma_ms ASC);
CREATE INDEX quality_proxy ON proxy_quality(proxy_id);
CREATE TABLE sources (
 source_id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL, spider_key TEXT NOT NULL, url TEXT NOT NULL, enabled INTEGER NOT NULL,
 last_published_at INTEGER, latest_task_id TEXT,
 last_result_at INTEGER, outcome TEXT, output_count INTEGER, inserted_count INTEGER,
 duration_ms REAL, pages_ok INTEGER, pages_failed INTEGER, error_code TEXT
);
"""


class NotFound(ValueError):
    pass


class LimitExceeded(ValueError):
    pass


class Database:
    def __init__(self, settings):
        self.settings = settings
        self.path = Path(settings.database).resolve()

    def connect(self, *, readonly=False):
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        if readonly:
            connection.execute("PRAGMA query_only=ON")
        return connection

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self.connect()
        try:
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            if tables:
                if (
                    connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                    or connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
                ):
                    raise ValueError("Not a current Proxypool database; use a new path")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA cache_size=-16384")
            if not tables:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + SCHEMA
                    + f"PRAGMA application_id={APPLICATION_ID};\nPRAGMA user_version={SCHEMA_VERSION};\nCOMMIT;"
                )
            if os.name != "nt":
                self.path.chmod(0o600)
            return connection
        except BaseException:
            connection.close()
            raise

    @contextmanager
    def reader(self):
        connection = self.connect(readonly=True)
        try:
            yield connection
        finally:
            connection.close()

    def targets(self):
        with self.reader() as db:
            return [
                dict(x, enabled=bool(x["enabled"]))
                for x in db.execute("SELECT * FROM targets ORDER BY target_id")
            ]

    def sources(self):
        with self.reader() as db:
            return [
                dict(x, enabled=bool(x["enabled"]))
                for x in db.execute("SELECT * FROM sources ORDER BY source_id")
            ]

    def candidates(self, target_id, limit, minimum):
        with self.reader() as db:
            rows = db.execute(
                """SELECT p.*,q.score,q.sample_count,q.result_bits,q.successful_latency_ewma_ms
              FROM proxy_quality q JOIN proxies p USING(proxy_id)
              JOIN targets t ON t.target_id=q.target_id
              WHERE q.target_id=? AND t.enabled=1 AND q.target_revision=t.revision AND q.score>=?
              ORDER BY q.score DESC,q.successful_latency_ewma_ms ASC LIMIT ?""",
                (target_id, minimum, limit),
            )
            return [dict(x, proxy_url=proxy_url(x["scheme"], x["host"], x["port"])) for x in rows]

    def quality_page(self, target_id, minimum=0.0, page=1, size=50):
        with self.reader() as db:
            total = db.execute(
                "SELECT COUNT(*) FROM proxy_quality WHERE target_id=? AND score>=?",
                (target_id, minimum),
            ).fetchone()[0]
            rows = db.execute(
                """SELECT p.*,q.score,q.sample_count,q.result_bits,q.feedback_count,
                 q.successful_latency_ewma_ms,q.last_feedback_at,q.last_test_scheduled_at
              FROM proxy_quality q JOIN proxies p USING(proxy_id)
              WHERE q.target_id=? AND q.score>=?
              ORDER BY q.score DESC,q.successful_latency_ewma_ms ASC LIMIT ? OFFSET ?""",
                (target_id, minimum, size, (page - 1) * size),
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item["success_count"] = item.pop("result_bits").bit_count()
                item["proxy_url"] = proxy_url(item["scheme"], item["host"], item["port"])
                items.append(item)
            return {"items": items, "total": total, "page": page, "size": size}

    def select_tests(self, target_id, now_ms, limit=None):
        """One target config, one LEFT JOIN: brand new endpoints remain visible."""
        with self.reader() as db:
            target = db.execute(
                "SELECT * FROM targets WHERE target_id=? AND enabled=1", (target_id,)
            ).fetchone()
            if target is None:
                return []
            cursor = db.execute(
                """SELECT p.*,q.target_revision,q.last_feedback_at,q.last_test_scheduled_at
              FROM proxies p LEFT JOIN proxy_quality q ON q.target_id=? AND q.proxy_id=p.proxy_id
              ORDER BY COALESCE(q.last_feedback_at,0),COALESCE(q.last_test_scheduled_at,0),p.proxy_id""",
                (target_id,),
            )
            result = []
            for row in cursor:
                current = row["target_revision"] == target["revision"]
                feedback = row["last_feedback_at"] if current else None
                scheduled = row["last_test_scheduled_at"] if current else None
                if needs_test(
                    feedback,
                    scheduled,
                    now_ms,
                    self.settings.min_test_interval_seconds * 1000,
                    self.settings.test_timeout_seconds * 1000,
                ):
                    result.append(
                        {
                            "proxy_id": row["proxy_id"],
                            "proxy_url": proxy_url(row["scheme"], row["host"], row["port"]),
                        }
                    )
                    if limit is not None and len(result) >= limit:
                        break
            return result

    def stats(self):
        with self.reader() as db:
            targets = [
                dict(row)
                for row in db.execute(
                    """SELECT t.target_id,t.name,t.test_url,t.enabled,t.revision,
                COUNT(q.proxy_id) quality_count,
                COALESCE(SUM(CASE WHEN q.score>=? THEN 1 ELSE 0 END),0) high_score_count
                FROM targets t LEFT JOIN proxy_quality q ON q.target_id=t.target_id AND q.target_revision=t.revision
                GROUP BY t.target_id ORDER BY t.target_id""",
                    (self.settings.candidate_min_score,),
                )
            ]
            return {
                "proxy_count": db.execute("SELECT COUNT(*) FROM proxies").fetchone()[0],
                "pool_limit": self.settings.pool_limit,
                "targets": targets,
                "source_count": db.execute(
                    "SELECT COUNT(*) FROM sources WHERE enabled=1"
                ).fetchone()[0],
                "candidate_min_score": self.settings.candidate_min_score,
            }

    def apply(self, connection, kind, data, now_ms):
        method = getattr(self, "_write_" + kind, None)
        if method is None:
            raise ValueError("Unknown internal write operation")
        return method(connection, data, now_ms)

    def _write_import(self, db, data, now):
        before = db.total_changes
        db.executemany(
            "INSERT INTO proxies(scheme,host,port) VALUES(:scheme,:host,:port) ON CONFLICT(scheme,host,port) DO NOTHING",
            data,
        )
        return {"inserted": db.total_changes - before}

    def _write_target_save(self, db, data, now):
        identifier = data.get("target_id")
        if identifier is None:
            if (
                db.execute("SELECT COUNT(*) FROM targets").fetchone()[0]
                >= self.settings.max_targets
            ):
                raise LimitExceeded("Target limit reached")
            identifier = db.execute(
                "INSERT INTO targets(name,test_url,enabled) VALUES(?,?,?)",
                (data["name"], data["test_url"], int(data["enabled"])),
            ).lastrowid
        else:
            old = db.execute("SELECT * FROM targets WHERE target_id=?", (identifier,)).fetchone()
            if old is None:
                raise NotFound("Target not found")
            changed = old["test_url"] != data["test_url"]
            db.execute(
                "UPDATE targets SET name=?,test_url=?,enabled=?,revision=revision+? WHERE target_id=?",
                (data["name"], data["test_url"], int(data["enabled"]), int(changed), identifier),
            )
            if changed:
                db.execute("DELETE FROM proxy_quality WHERE target_id=?", (identifier,))
        return dict(db.execute("SELECT * FROM targets WHERE target_id=?", (identifier,)).fetchone())

    def _write_target_delete(self, db, data, now):
        if not db.execute("DELETE FROM targets WHERE target_id=?", (data,)).rowcount:
            raise NotFound("Target not found")
        return {"deleted": 1}

    def _write_source_save(self, db, data, now):
        identifier = data.get("source_id")
        if identifier is None:
            if (
                db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
                >= self.settings.max_sources
            ):
                raise LimitExceeded("Source limit reached")
            identifier = db.execute(
                "INSERT INTO sources(name,spider_key,url,enabled) VALUES(?,?,?,?)",
                (data["name"], data["spider_key"], data["url"], int(data["enabled"])),
            ).lastrowid
        elif not db.execute(
            "UPDATE sources SET name=?,spider_key=?,url=?,enabled=? WHERE source_id=?",
            (data["name"], data["spider_key"], data["url"], int(data["enabled"]), identifier),
        ).rowcount:
            raise NotFound("Source not found")
        return dict(db.execute("SELECT * FROM sources WHERE source_id=?", (identifier,)).fetchone())

    def _write_source_delete(self, db, data, now):
        if not db.execute("DELETE FROM sources WHERE source_id=?", (data,)).rowcount:
            raise NotFound("Source not found")
        return {"deleted": 1}

    def _write_source_published(self, db, data, now):
        db.execute(
            "UPDATE sources SET latest_task_id=?,last_published_at=? WHERE source_id=?",
            (data["task_id"], data["scheduled_at"], data["source_id"]),
        )
        return {"updated": 1}

    def _write_source_result(self, db, data, now):
        if (
            db.execute("SELECT 1 FROM sources WHERE source_id=?", (data["source_id"],)).fetchone()
            is None
        ):
            return {"ignored": True, "inserted": 0}
        result = self._write_import(db, data["proxies"], now)
        if data["final"]:
            db.execute(
                """UPDATE sources SET latest_task_id=?,last_result_at=?,outcome=?,output_count=?,
              inserted_count=?,duration_ms=?,pages_ok=?,pages_failed=?,error_code=? WHERE source_id=?""",
                (
                    data["task_id"],
                    now,
                    data["outcome"],
                    data.get("output_count")
                    if data.get("output_count") is not None
                    else len(data["proxies"]),
                    data.get("total_inserted")
                    if data.get("total_inserted") is not None
                    else result["inserted"],
                    data["duration_ms"],
                    data["pages_ok"],
                    data["pages_failed"],
                    data["error_code"],
                    data["source_id"],
                ),
            )
        return result

    def _write_feedback(self, db, data, now):
        accepted = ignored = 0
        grouped = {}
        targets = {x["target_id"]: dict(x) for x in db.execute("SELECT * FROM targets")}
        for record in data:
            target = targets.get(record["target_id"])
            if (
                target is None
                or target["revision"] != record["target_revision"]
                or record["outcome"] not in ("response", "request_error")
                or db.execute(
                    "SELECT 1 FROM proxies WHERE proxy_id=?", (record["proxy_id"],)
                ).fetchone()
                is None
            ):
                ignored += 1
                continue
            key = (record["target_id"], record["proxy_id"])
            if key not in grouped:
                row = db.execute(
                    "SELECT * FROM proxy_quality WHERE target_id=? AND proxy_id=?", key
                ).fetchone()
                grouped[key] = (
                    dict(row)
                    if row
                    else dict(
                        result_bits=0,
                        sample_count=0,
                        feedback_count=0,
                        successful_latency_ewma_ms=None,
                        last_test_scheduled_at=None,
                    )
                )
            row = grouped[key]
            success = record["outcome"] == "response" and record["http_status"] == 200
            row["result_bits"] = ((row["result_bits"] << 1) | int(success)) & (
                (1 << self.settings.window_size) - 1
            )
            row["sample_count"] = min(row["sample_count"] + 1, self.settings.window_size)
            row["feedback_count"] += 1
            if success:
                old, value = row["successful_latency_ewma_ms"], record["latency_ms"]
                row["successful_latency_ewma_ms"] = (
                    value
                    if old is None
                    else (
                        (1 - self.settings.latency_alpha) * old
                        + self.settings.latency_alpha * value
                    )
                )
            accepted += 1
        for (target_id, proxy_id), row in grouped.items():
            score = wilson(
                row["result_bits"].bit_count(), row["sample_count"], self.settings.wilson_z
            )
            db.execute(
                """INSERT INTO proxy_quality(target_id,proxy_id,target_revision,result_bits,
                sample_count,feedback_count,score,successful_latency_ewma_ms,last_feedback_at,last_test_scheduled_at)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(target_id,proxy_id) DO UPDATE SET
                result_bits=excluded.result_bits,sample_count=excluded.sample_count,
                feedback_count=excluded.feedback_count,score=excluded.score,
                successful_latency_ewma_ms=excluded.successful_latency_ewma_ms,last_feedback_at=excluded.last_feedback_at""",
                (
                    target_id,
                    proxy_id,
                    targets[target_id]["revision"],
                    row["result_bits"],
                    row["sample_count"],
                    row["feedback_count"],
                    score,
                    row["successful_latency_ewma_ms"],
                    now,
                    row["last_test_scheduled_at"],
                ),
            )
        return {"accepted": accepted, "ignored": ignored}

    def _write_scheduled(self, db, data, now):
        target = db.execute(
            "SELECT * FROM targets WHERE target_id=?", (data["target_id"],)
        ).fetchone()
        if target is None or target["revision"] != data["target_revision"]:
            return {"updated": 0}
        updated = 0
        for proxy_id in data["proxy_ids"]:
            if db.execute("SELECT 1 FROM proxies WHERE proxy_id=?", (proxy_id,)).fetchone():
                db.execute(
                    """INSERT INTO proxy_quality(target_id,proxy_id,target_revision,last_test_scheduled_at)
                 VALUES(?,?,?,?) ON CONFLICT(target_id,proxy_id) DO UPDATE SET
                 last_test_scheduled_at=MAX(COALESCE(proxy_quality.last_test_scheduled_at,0),excluded.last_test_scheduled_at)""",
                    (data["target_id"], proxy_id, data["target_revision"], data["scheduled_at"]),
                )
                updated += 1
        return {"updated": updated}

    def _write_plan_eviction(self, db, data, now):
        before = db.execute("SELECT COUNT(*) FROM proxies").fetchone()[0]
        excess = max(0, before - self.settings.pool_limit)
        rows = (
            []
            if not excess
            else db.execute(
                """SELECT q.proxy_id,SUM(COALESCE(q.score,0)) total_score,
            SUM(q.feedback_count) total_count FROM proxy_quality q JOIN targets t ON t.target_id=q.target_id
            WHERE t.enabled=1 AND q.target_revision=t.revision GROUP BY q.proxy_id
            HAVING total_count>=? ORDER BY total_score ASC,q.proxy_id ASC LIMIT ?""",
                (self.settings.eviction_min_feedback, excess),
            ).fetchall()
        )
        return {"before": before, "requested": excess, "proxy_ids": [x["proxy_id"] for x in rows]}

    def _write_delete_proxies(self, db, data, now):
        deleted = 0
        for identifier in data:
            deleted += db.execute("DELETE FROM proxies WHERE proxy_id=?", (identifier,)).rowcount
        return {"deleted": deleted}
