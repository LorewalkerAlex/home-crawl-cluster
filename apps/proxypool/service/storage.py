"""Small local SQLite verification slice, not a production M05 API.

One serialized write entry, finite observations/receipts/tasks and explicit
transactions. A scoring function must be supplied by the caller; this module
does not select the project's final scoring policy.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import ipaddress
import math
from pathlib import Path
import sqlite3
import statistics
import threading
import uuid

from .contracts import MAX_RECORDS, Result, ValidationError, identifier, integer


class ConflictError(ValueError):
    pass


class StaleCheckError(ValueError):
    pass


class CapacityError(ValueError):
    pass


@dataclass(frozen=True)
class Limits:
    sample_limit: int = 50
    sample_window_ms: int = 86_400_000
    check_interval_ms: int = 300_000
    check_timeout_ms: int = 60_000
    receipt_retention_ms: int = 172_800_000
    clock_skew_ms: int = 60_000
    max_proxies: int = 1000
    max_targets: int = 20
    max_pairs: int = 5000
    max_batches: int = 200
    max_checks: int = 10_000
    max_receipts: int = 20_000
    max_database_bytes: int = 16 * 1024 * 1024

    def __post_init__(self):
        for key, value in vars(self).items():
            integer(value, key, 1)
        if self.receipt_retention_ms < self.sample_window_ms + self.clock_skew_ms:
            raise ValidationError("Receipt retention must cover the accepted feedback window")
        if self.max_database_bytes < 1024 * 1024:
            raise ValidationError("Database limit must be at least 1 MiB")


SCHEMA = """
CREATE TABLE proxies (
    proxy_id TEXT PRIMARY KEY, host TEXT NOT NULL, port INTEGER NOT NULL,
    scheme TEXT NOT NULL, UNIQUE(scheme,host,port)
);
CREATE TABLE targets (target_id TEXT PRIMARY KEY, revision INTEGER NOT NULL);
CREATE TABLE quality (
    proxy_id TEXT NOT NULL REFERENCES proxies(proxy_id),
    target_id TEXT NOT NULL REFERENCES targets(target_id), revision INTEGER NOT NULL,
    score REAL, sample_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0, latency_median_ms REAL,
    last_feedback_ms INTEGER, active_check_id TEXT,
    PRIMARY KEY(proxy_id,target_id,revision)
);
CREATE TABLE batches (
    batch_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
    target_id TEXT NOT NULL REFERENCES targets(target_id), revision INTEGER NOT NULL,
    requested_count INTEGER NOT NULL, created_at_ms INTEGER NOT NULL,
    deadline_ms INTEGER NOT NULL, task_id TEXT UNIQUE
);
CREATE TABLE checks (
    check_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(batch_id) ON DELETE CASCADE,
    proxy_id TEXT NOT NULL, target_id TEXT NOT NULL, revision INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','reported','expired')),
    deadline_ms INTEGER NOT NULL,
    FOREIGN KEY(proxy_id,target_id,revision) REFERENCES quality(proxy_id,target_id,revision)
);
CREATE UNIQUE INDEX one_pending_check ON checks(proxy_id,target_id,revision) WHERE status='pending';
CREATE INDEX checks_due ON checks(status,deadline_ms);
CREATE INDEX checks_batch ON checks(batch_id);
CREATE INDEX quality_due ON quality(target_id,revision,active_check_id,last_feedback_ms);
CREATE INDEX quality_rank ON quality(target_id,revision,score DESC);
CREATE TABLE observations (
    origin TEXT NOT NULL, result_id TEXT NOT NULL, proxy_id TEXT NOT NULL,
    target_id TEXT NOT NULL, revision INTEGER NOT NULL, effective_at_ms INTEGER NOT NULL,
    success INTEGER NOT NULL, latency_ms INTEGER,
    PRIMARY KEY(origin,result_id),
    FOREIGN KEY(proxy_id,target_id,revision) REFERENCES quality(proxy_id,target_id,revision)
);
CREATE INDEX samples_pair ON observations(proxy_id,target_id,revision,effective_at_ms DESC);
CREATE INDEX samples_due ON observations(effective_at_ms);
CREATE TABLE receipts (
    origin TEXT NOT NULL, result_id TEXT NOT NULL, digest TEXT NOT NULL,
    expires_at_ms INTEGER NOT NULL, PRIMARY KEY(origin,result_id)
);
CREATE INDEX receipts_due ON receipts(expires_at_ms);
PRAGMA user_version=2;
PRAGMA application_id=1347964213;
"""


class LocalStore:
    def __init__(self, path, *, score_fn, limits=None):
        self.limits = limits or Limits()
        self._score_fn = score_fn
        self._lock = threading.RLock()
        self.path = Path(path)
        self._db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        try:
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            application = self._db.execute("PRAGMA application_id").fetchone()[0]
            tables = self._db.execute("SELECT name FROM sqlite_master").fetchall()
            fresh = version == 0 and not tables and application == 0
            if not fresh and (version != 2 or application != 1347964213):
                raise ValidationError("Unsupported database; use a fresh local test database")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.execute("PRAGMA busy_timeout=5000")
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("PRAGMA journal_size_limit=1048576")
            page_size = self._db.execute("PRAGMA page_size").fetchone()[0]
            max_pages = self.limits.max_database_bytes // page_size
            if self._db.execute(f"PRAGMA max_page_count={max_pages}").fetchone()[0] > max_pages:
                raise CapacityError("Existing database exceeds the configured page limit")
            if fresh:
                self._db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nCOMMIT;")
        except Exception:
            self._db.close()
            raise

    def close(self):
        with self._lock:
            self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    @contextmanager
    def _write(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
                for table, maximum in (("proxies", self.limits.max_proxies),
                                       ("targets", self.limits.max_targets),
                                       ("quality", self.limits.max_pairs),
                                       ("batches", self.limits.max_batches),
                                       ("checks", self.limits.max_checks),
                                       ("receipts", self.limits.max_receipts)):
                    if self._db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] > maximum:
                        raise CapacityError(f"Capacity exceeded: {table}")
                self._db.execute("COMMIT")
            except Exception:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise

    def register_proxy(self, proxy_id, host, port, scheme="http"):
        identifier(proxy_id, "proxy_id")
        integer(port, "port", 1, 65535)
        if scheme not in {"http", "https"}:
            raise ValidationError("Unsupported proxy scheme in this local slice")
        try:
            host = ipaddress.ip_address(host).compressed
        except ValueError:
            raise ValidationError("host must be an IP address") from None
        with self._write():
            existing = self._db.execute("SELECT * FROM proxies WHERE proxy_id=?", (proxy_id,)).fetchone()
            if existing and (existing["host"], existing["port"], existing["scheme"]) != (host, port, scheme):
                raise ConflictError("Proxy identity conflict")
            self._db.execute("INSERT INTO proxies VALUES(?,?,?,?) ON CONFLICT DO NOTHING",
                             (proxy_id, host, port, scheme))
            canonical = self._db.execute("SELECT proxy_id FROM proxies WHERE scheme=? AND host=? AND port=?",
                                         (scheme, host, port)).fetchone()[0]
        return canonical

    def register_target(self, target_id, revision=1):
        identifier(target_id, "target_id")
        integer(revision, "revision", 1)
        with self._write():
            existing = self._db.execute("SELECT revision FROM targets WHERE target_id=?", (target_id,)).fetchone()
            if existing and existing[0] != revision:
                raise ConflictError("Target revision migration is outside this local slice")
            self._db.execute("INSERT INTO targets VALUES(?,?) ON CONFLICT DO NOTHING", (target_id, revision))

    def _revision(self, target_id):
        row = self._db.execute("SELECT revision FROM targets WHERE target_id=?", (target_id,)).fetchone()
        if row is None:
            raise ValidationError("Unknown target")
        return row[0]

    def _pair(self, proxy_id, target_id, revision):
        if self._revision(target_id) != revision:
            raise ValidationError("Obsolete target revision")
        self._db.execute("INSERT INTO quality(proxy_id,target_id,revision) VALUES(?,?,?) ON CONFLICT DO NOTHING",
                         (proxy_id, target_id, revision))

    def reserve_checks(self, request_id, target_id, limit, *, now_ms):
        identifier(request_id, "request_id")
        identifier(target_id, "target_id")
        integer(limit, "limit", 1, MAX_RECORDS)
        integer(now_ms, "now_ms")
        with self._write():
            existing = self._db.execute("SELECT * FROM batches WHERE request_id=?", (request_id,)).fetchone()
            if existing:
                if (existing["target_id"], existing["requested_count"]) != (target_id, limit):
                    raise ConflictError("Request identity conflict")
                batch_id = existing["batch_id"]
            else:
                revision = self._revision(target_id)
                rows = self._db.execute("""
                    SELECT p.proxy_id FROM proxies p LEFT JOIN quality q
                      ON q.proxy_id=p.proxy_id AND q.target_id=? AND q.revision=?
                    WHERE q.active_check_id IS NULL
                      AND (q.last_feedback_ms IS NULL OR q.last_feedback_ms<=?)
                    ORDER BY q.last_feedback_ms IS NOT NULL,q.last_feedback_ms,p.proxy_id LIMIT ?
                """, (target_id, revision, now_ms-self.limits.check_interval_ms, limit)).fetchall()
                batch_id = uuid.uuid4().hex
                deadline = now_ms + self.limits.check_timeout_ms
                self._db.execute("""INSERT INTO batches(batch_id,request_id,target_id,revision,requested_count,
                    created_at_ms,deadline_ms) VALUES(?,?,?,?,?,?,?)""",
                                 (batch_id, request_id, target_id, revision, limit, now_ms, deadline))
                for row in rows:
                    self._pair(row[0], target_id, revision)
                    check_id = uuid.uuid4().hex
                    self._db.execute("INSERT INTO checks VALUES(?,?,?,?,?,'pending',?)",
                                     (check_id, batch_id, row[0], target_id, revision, deadline))
                    self._db.execute("UPDATE quality SET active_check_id=? WHERE proxy_id=? AND target_id=? AND revision=?",
                                     (check_id, row[0], target_id, revision))
        return self.read_batch(batch_id)

    def bind_task(self, batch_id, task_id):
        """Trusted publisher setup for local tests; not Crawlab authentication."""
        identifier(batch_id, "batch_id")
        identifier(task_id, "task_id")
        with self._write():
            row = self._db.execute("SELECT task_id FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
            if row is None:
                raise ValidationError("Unknown batch")
            if row[0] not in {None, task_id}:
                raise ConflictError("Batch is already bound to another task")
            other = self._db.execute("SELECT batch_id FROM batches WHERE task_id=?", (task_id,)).fetchone()
            if other and other[0] != batch_id:
                raise ConflictError("Task is already bound to another batch")
            self._db.execute("UPDATE batches SET task_id=? WHERE batch_id=?", (task_id, batch_id))
        return {"committed": True, "batch_id": batch_id, "task_id": task_id}

    def read_batch(self, batch_id):
        with self._lock:
            row = self._db.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
            if row is None:
                raise ValidationError("Unknown batch")
            items = self._db.execute("""SELECT c.check_id,c.proxy_id,c.status,p.host,p.port,p.scheme
                FROM checks c JOIN proxies p USING(proxy_id) WHERE batch_id=? ORDER BY c.proxy_id""",
                                     (batch_id,)).fetchall()
            return {**dict(row), "members": [dict(item) for item in items]}

    def _summarize(self, pair, now_ms):
        self._db.execute("""DELETE FROM observations WHERE proxy_id=? AND target_id=? AND revision=? AND
            (effective_at_ms<=? OR (origin,result_id) NOT IN
             (SELECT origin,result_id FROM observations WHERE proxy_id=? AND target_id=? AND revision=?
              ORDER BY effective_at_ms DESC,origin,result_id LIMIT ?))""",
                         (*pair, now_ms-self.limits.sample_window_ms, *pair, self.limits.sample_limit))
        rows = self._db.execute("""SELECT success,latency_ms FROM observations
            WHERE proxy_id=? AND target_id=? AND revision=?""", pair).fetchall()
        count = len(rows)
        successes = sum(row[0] for row in rows)
        score = self._score_fn(successes, count)
        if (count == 0 and score is not None) or (count > 0 and
                (type(score) not in {int, float} or not math.isfinite(score) or not 0 <= score <= 100)):
            raise ValidationError("Scoring function returned an invalid score")
        latencies = [row[1] for row in rows if row[0] and row[1] is not None]
        median = statistics.median(latencies) if latencies else None
        self._db.execute("""UPDATE quality SET sample_count=?,success_count=?,score=?,latency_median_ms=?
            WHERE proxy_id=? AND target_id=? AND revision=?""", (count, successes, score, median, *pair))

    def _record(self, origin, records, now_ms, batch_id=None, task_id=None):
        integer(now_ms, "now_ms")
        if not isinstance(records, (list, tuple)) or not 1 <= len(records) <= MAX_RECORDS:
            raise ValidationError("Invalid result batch size")
        if any(not isinstance(item, Result) for item in records):
            raise ValidationError("Result objects required")
        if len({item.result_id for item in records}) != len(records):
            raise ValidationError("Duplicate result identity in one batch")
        accepted, duplicates, affected = 0, 0, set()
        with self._write():
            if origin == "active":
                batch = self._db.execute("SELECT task_id FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
                if batch is None:
                    raise ValidationError("Unknown batch")
                if (task_id is not None or batch[0] is not None) and batch[0] != task_id:
                    raise ConflictError("Callback task does not match the publisher binding")
            self._db.execute("""DELETE FROM receipts WHERE (origin,result_id) IN
                (SELECT origin,result_id FROM receipts WHERE expires_at_ms<=? ORDER BY expires_at_ms LIMIT ?)""",
                             (now_ms, MAX_RECORDS))
            for item in records:
                digest = item.fingerprint(origin, batch_id)
                receipt = self._db.execute("SELECT digest,expires_at_ms FROM receipts WHERE origin=? AND result_id=?",
                                           (origin, item.result_id)).fetchone()
                if receipt and receipt["expires_at_ms"] <= now_ms:
                    self._db.execute("DELETE FROM receipts WHERE origin=? AND result_id=?", (origin, item.result_id))
                    receipt = None
                if receipt:
                    if receipt[0] != digest:
                        raise ConflictError("Result identity conflict")
                    duplicates += 1
                    continue
                if not now_ms-self.limits.sample_window_ms < item.observed_at_ms <= now_ms+self.limits.clock_skew_ms:
                    raise ValidationError("Observation outside the accepted time window")
                pair = (item.proxy_id, item.target_id, item.revision)
                self._pair(*pair)
                if origin == "active":
                    check = self._db.execute("SELECT * FROM checks WHERE check_id=?", (item.result_id,)).fetchone()
                    if (check is None or check["batch_id"] != batch_id or
                            (check["proxy_id"], check["target_id"], check["revision"]) != pair):
                        raise ValidationError("Result does not match the fixed test member")
                    active = self._db.execute("""SELECT active_check_id FROM quality
                        WHERE proxy_id=? AND target_id=? AND revision=?""", pair).fetchone()[0]
                    if check["status"] != "pending" or check["deadline_ms"] <= now_ms or active != item.result_id:
                        raise StaleCheckError("Test is no longer current")
                    self._db.execute("UPDATE checks SET status='reported' WHERE check_id=?", (item.result_id,))
                    self._db.execute("""UPDATE quality SET active_check_id=NULL
                        WHERE proxy_id=? AND target_id=? AND revision=? AND active_check_id=?""", (*pair, item.result_id))
                self._db.execute("INSERT INTO receipts VALUES(?,?,?,?)",
                                 (origin, item.result_id, digest, now_ms+self.limits.receipt_retention_ms))
                if item.scorable:
                    effective = min(item.observed_at_ms, now_ms)
                    self._db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?)",
                                     (origin, item.result_id, *pair, effective, int(item.success), item.latency_ms))
                    self._db.execute("""UPDATE quality SET last_feedback_ms=
                        CASE WHEN last_feedback_ms IS NULL OR last_feedback_ms<? THEN ? ELSE last_feedback_ms END
                        WHERE proxy_id=? AND target_id=? AND revision=?""", (effective, effective, *pair))
                    affected.add(pair)
                accepted += 1
            for pair in affected:
                self._summarize(pair, now_ms)
        return {"committed": True, "accepted": accepted, "duplicates": duplicates}

    def record_test_results(self, batch_id, records, *, now_ms, task_id=None):
        identifier(batch_id, "batch_id")
        if task_id is not None:
            identifier(task_id, "task_id")
        return self._record("active", records, now_ms, batch_id, task_id)

    def record_usage(self, records, *, now_ms):
        """Internal entry only; caller authentication/allocation validation is not implemented."""
        return self._record("usage", records, now_ms)

    def quality(self, proxy_id, target_id):
        with self._lock:
            revision = self._revision(target_id)
            row = self._db.execute("SELECT * FROM quality WHERE proxy_id=? AND target_id=? AND revision=?",
                                   (proxy_id, target_id, revision)).fetchone()
            if row is None:
                return None
            return {**dict(row), "test_status": "pending" if row["active_check_id"] else "available"}

    def get_proxies(self, target_id, *, limit=10, min_score=0):
        integer(limit, "limit", 1, MAX_RECORDS)
        if type(min_score) not in {int, float} or not math.isfinite(min_score) or not 0 <= min_score <= 100:
            raise ValidationError("Invalid min_score")
        with self._lock:
            revision = self._revision(target_id)
            rows = self._db.execute("""SELECT p.*,q.score,q.sample_count,q.last_feedback_ms FROM quality q
                JOIN proxies p USING(proxy_id) WHERE q.target_id=? AND q.revision=? AND q.score>=?
                ORDER BY q.score DESC,q.latency_median_ms IS NULL,q.latency_median_ms,p.proxy_id LIMIT ?""",
                                    (target_id, revision, min_score, limit)).fetchall()
            return [dict(row) for row in rows]

    def expire_checks(self, *, now_ms, limit=MAX_RECORDS):
        integer(now_ms, "now_ms")
        integer(limit, "limit", 1, MAX_RECORDS)
        with self._write():
            rows = self._db.execute("SELECT * FROM checks WHERE status='pending' AND deadline_ms<=? ORDER BY deadline_ms LIMIT ?",
                                    (now_ms, limit)).fetchall()
            for row in rows:
                self._db.execute("UPDATE checks SET status='expired' WHERE check_id=?", (row["check_id"],))
                self._db.execute("""UPDATE quality SET active_check_id=NULL
                    WHERE proxy_id=? AND target_id=? AND revision=? AND active_check_id=?""",
                                 (row["proxy_id"], row["target_id"], row["revision"], row["check_id"]))
        return len(rows)

    def maintenance(self, *, now_ms, limit=MAX_RECORDS):
        """Bounded expiry/refresh/cleanup; never advances the last real-feedback time."""
        integer(now_ms, "now_ms")
        integer(limit, "limit", 1, MAX_RECORDS)
        expired = self.expire_checks(now_ms=now_ms, limit=limit)
        with self._write():
            pairs = self._db.execute("""SELECT DISTINCT proxy_id,target_id,revision FROM observations
                WHERE effective_at_ms<=? LIMIT ?""", (now_ms-self.limits.sample_window_ms, limit)).fetchall()
            for row in pairs:
                self._summarize(tuple(row), now_ms)
            self._db.execute("""DELETE FROM receipts WHERE (origin,result_id) IN
                (SELECT origin,result_id FROM receipts WHERE expires_at_ms<=? ORDER BY expires_at_ms LIMIT ?)""",
                             (now_ms, limit))
            self._db.execute("""DELETE FROM batches WHERE batch_id IN
                (SELECT b.batch_id FROM batches b WHERE b.deadline_ms+?<=? AND NOT EXISTS
                 (SELECT 1 FROM checks c WHERE c.batch_id=b.batch_id AND c.status='pending') LIMIT ?)""",
                             (self.limits.receipt_retention_ms, now_ms, limit))
        return {"expired_checks": expired, "refreshed_pairs": len(pairs)}

    def diagnostics(self):
        with self._lock:
            counts = {name: self._db.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                      for name in ("proxies", "targets", "quality", "batches", "checks", "observations", "receipts")}
            pragmas = {name: self._db.execute(f"PRAGMA {name}").fetchone()[0]
                       for name in ("journal_mode", "foreign_keys", "synchronous", "max_page_count", "page_size")}
            return {"counts": counts, "pragmas": pragmas}
