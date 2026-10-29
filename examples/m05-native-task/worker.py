"""Synthetic Worker program: no real proxy or target website requests.

CRAWLAB_TASK_ID must be supplied by Crawlab (a test double in local tests).
The program exits successfully only after M05 confirms the matching COMMIT.
"""

import argparse
from datetime import datetime, timezone
import json
import os
import sys
import time

from native_task import JsonClient, TaskError, batch_identifier, object_id


def run(client, batch_id, task_id, *, wait_seconds=15, monotonic=time.monotonic, sleep=time.sleep):
    batch_identifier(batch_id)
    object_id(task_id)
    if not 0 < wait_seconds <= 30:
        raise TaskError("Binding wait must be between zero and 30 seconds")
    deadline = monotonic() + wait_seconds
    while True:
        batch = client.request("GET", "/internal/v1/check-batches/" + batch_id)["batch"]
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TaskError("Publisher did not bind the task before the readiness deadline")
        if batch.get("batch_id") != batch_id:
            raise TaskError("Batch identity mismatch")
        bound = batch.get("task_id")
        if bound is not None:
            if bound != task_id:
                raise TaskError("Batch is bound to another task")
            break
        # Waiting for publication metadata is not retrying a proxy test.
        sleep(min(0.1, remaining))
    members = batch.get("members")
    if not isinstance(members, list) or len(members) != 3:
        raise TaskError("This synthetic fixture requires exactly three reserved checks")
    if any(item.get("status") != "pending" for item in members):
        raise TaskError("Synthetic checks are no longer pending")
    observed_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    records = []
    for member, status in zip(members, (200, 503, 200)):
        records.append({"check_id": member["check_id"], "proxy_id": member["proxy_id"],
                        "target_id": batch["target_id"], "target_revision": batch["revision"],
                        "observed_at": observed_at, "outcome": "response", "http_status": status,
                        "latency_ms": 80, "error_code": None})
    payload = {"schema_version": 1, "batch_id": batch_id, "task_id": task_id, "records": records}
    # Exactly one callback request: ambiguous transport failures exit with an error.
    ack = client.request("POST", "/internal/v1/check-results", payload)
    if (ack.get("committed") is not True or ack.get("persisted") is not True or
            ack.get("batch_id") != batch_id or ack.get("task_id") != task_id or
            ack.get("record_count") != 3 or ack.get("accepted") != 3 or ack.get("duplicates") != 0):
        raise TaskError("M05 did not confirm this synthetic batch's committed results")
    return {"check": "PASS", "synthetic": True, "task_id": task_id, "batch_id": batch_id,
            "sent": 3, "persisted": True, "committed": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--m05-base-url", required=True)
    args = parser.parse_args()
    try:
        result = run(JsonClient(args.m05_base_url), args.batch_id, os.environ.get("CRAWLAB_TASK_ID"))
        print(json.dumps(result), flush=True)
        return 0
    except (TaskError, KeyError, TypeError):
        print(json.dumps({"check": "FAIL", "reason": "task_or_callback_not_confirmed"}), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
