"""One bounded queue and one SQLite owner thread; no persistent message broker."""

from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import dataclass
import queue
import sqlite3
import threading
import time


class Busy(RuntimeError):
    pass


@dataclass
class Operation:
    kind: str
    payload: object
    size: int
    urgent: bool
    enqueued_at: float
    future: Future


class Writer:
    def __init__(self, database, clock=None):
        self.database = database
        self.settings = database.settings
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.queue = queue.Queue(maxsize=self.settings.queue_max_requests)
        self._guard = threading.Lock()
        self._records = 0
        self._accepting = True
        self._ready = Future()
        self._recent = OrderedDict()
        self.commits = 0
        self._thread = threading.Thread(target=self._run, name="proxypool-writer", daemon=False)
        self._thread.start()
        self._ready.result(timeout=15)

    def submit(self, kind, payload, *, urgent=False):
        size = (
            len(payload)
            if kind in ("feedback", "import", "delete_proxies")
            else (
                max(1, len(payload["proxies"]))
                if kind == "source_result"
                else len(payload["proxy_ids"])
                if kind == "scheduled"
                else 1
            )
        )
        if not 1 <= size <= self.settings.max_request_records:
            raise ValueError("Write package exceeds configured record limit")
        operation = Operation(kind, payload, size, urgent, time.monotonic(), Future())
        with self._guard:
            if not self._accepting or self._records + size > self.settings.queue_max_records:
                raise Busy("Write queue busy")
            try:
                self.queue.put_nowait(operation)
            except queue.Full:
                raise Busy("Write queue busy") from None
            self._records += size
        return operation.future

    def _take(self, timeout=None):
        value = self.queue.get(timeout=timeout)
        if value is not None:
            with self._guard:
                self._records -= value.size
        return value

    def _expire_ids(self):
        now = time.monotonic()
        while self._recent and next(iter(self._recent.values()))[0] <= now:
            self._recent.popitem(last=False)

    def _commit(self, connection, batch):
        self._expire_ids()
        processed = {}
        results = []
        transacted = False
        try:
            for operation in batch:
                kind, data = operation.kind, operation.payload
                duplicates = 0
                if kind == "feedback":
                    unique = []
                    for record in data:
                        key = ("feedback", record["result_id"])
                        if key in self._recent or key in processed:
                            duplicates += 1
                        else:
                            processed[key] = True
                            unique.append(record)
                    data = unique
                    if not data:
                        results.append({"accepted": 0, "ignored": 0, "duplicate_count": duplicates})
                        continue
                    if all(record["outcome"] in ("local_error", "cancelled") for record in data):
                        results.append(
                            {"accepted": 0, "ignored": len(data), "duplicate_count": duplicates}
                        )
                        continue
                source_key = None
                if kind == "source_result":
                    source_key = ("source", data["source_id"], data["task_id"], data["part"])
                    old = self._recent.get(source_key)
                    if old or source_key in processed:
                        result = dict(old[1] if old else processed[source_key], duplicate=True)
                        results.append(result)
                        continue
                if not connection.in_transaction:
                    connection.execute("BEGIN IMMEDIATE")
                    transacted = True
                result = self.database.apply(connection, kind, data, self.clock())
                if kind == "feedback":
                    result["duplicate_count"] = duplicates
                if source_key is not None:
                    processed[source_key] = dict(result)
                results.append(result)
            if connection.in_transaction:
                connection.execute("COMMIT")
        except BaseException as error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            failed_operation = operation
            for operation in batch:
                if not operation.future.done():
                    failure = (
                        error
                        if operation is failed_operation or isinstance(error, sqlite3.Error)
                        else Busy("Batch rolled back; retry original request")
                    )
                    operation.future.set_exception(failure)
            return
        if transacted:
            self.commits += 1
        expiry = time.monotonic() + self.settings.feedback_retry_window_seconds
        for key, result in processed.items():
            self._recent[key] = (expiry, result)
        while len(self._recent) > self.settings.dedup_max_ids:
            self._recent.popitem(last=False)
        for operation, result in zip(batch, results):
            if not operation.future.done():
                operation.future.set_result(dict(result, committed=True))

    def _run(self):
        try:
            connection = self.database.initialize()
        except BaseException as error:
            self._ready.set_exception(error)
            return
        self._ready.set_result(True)
        carry = None
        stopping = False
        try:
            while not stopping:
                first = carry or self._take()
                carry = None
                if first is None:
                    break
                batch, count = [first], first.size
                deadline = first.enqueued_at + self.settings.write_flush_seconds
                while (
                    not batch[-1].urgent
                    and count < self.settings.write_batch_records
                    and len(batch) < self.settings.queue_max_requests
                ):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        candidate = self._take(timeout=remaining)
                    except queue.Empty:
                        break
                    if candidate is None:
                        stopping = True
                        break
                    if count + candidate.size > self.settings.write_batch_records:
                        carry = candidate
                        break
                    batch.append(candidate)
                    count += candidate.size
                self._commit(connection, batch)
        finally:
            connection.close()

    def close(self):
        with self._guard:
            self._accepting = False
        self.queue.put(None)
        self._thread.join()

    def health(self):
        with self._guard:
            return {
                "writer_alive": self._thread.is_alive(),
                "queued_records": self._records,
                "queued_requests": self.queue.qsize(),
                "commits": self.commits,
                "dedup_ids": len(self._recent),
            }
