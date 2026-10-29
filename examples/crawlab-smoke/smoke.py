"""Crawlab execution and SDK smoke check; no external website requests."""

import os
import uuid

from crawlab import save_item


def main():
    print("LAN_PATH_CHECK_20261026", flush=True)
    task_id = os.environ.get("CRAWLAB_TASK_ID")
    if not task_id:
        raise RuntimeError("Run this file as a Crawlab task: CRAWLAB_TASK_ID is missing")

    marker = uuid.uuid4().hex
    print(
        "SMOKE_START task_id={} host={} marker={} expected=3".format(
            task_id, os.environ.get("HOSTNAME", "unknown"), marker
        ),
        flush=True,
    )

    for sequence in range(1, 4):
        save_item({
            "kind": "platform_smoke",
            "run_marker": marker,
            "sequence": sequence,
            "message": "hello_crawlab",
        })
        print("SMOKE_ITEM_SENT sequence={}".format(sequence), flush=True)

    print("SMOKE_SDK_CALLS_RETURNED expected=3; verify this task's Data tab", flush=True)


if __name__ == "__main__":
    main()
