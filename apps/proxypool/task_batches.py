"""Bound callback JSON by both member count and the receiver's byte ceiling."""

import json


def callback_chunks(records, make_payload, job):
    count = int(job["max_request_records"])
    ceiling = int(job.get("max_request_bytes", 262144))
    offset = part = 0
    while offset < len(records):
        chunk = records[offset : offset + count]
        while True:
            payload = make_payload(chunk, part)
            if len(json.dumps(payload).encode("utf-8")) <= ceiling:
                break
            if len(chunk) <= 1:
                raise ValueError(
                    "One callback record exceeds the receiver byte ceiling"
                )
            chunk = chunk[: len(chunk) // 2]
        yield payload
        offset += len(chunk)
        part += 1
