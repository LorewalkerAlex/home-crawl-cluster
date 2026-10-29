"""User-run once: register two source-only programs and verify their complete files."""

import argparse
import json
from pathlib import Path
import sys
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apps.proxypool.service.config import Settings, secret  # noqa: E402 - direct script entry resolves its repository root
from integrations.crawlab.program import sync_program, program_command  # noqa: E402 - direct script entry resolves its repository root


def register(base_url, token, *, python="python", settings_file=None, transport=None):
    if not token:
        raise ValueError("Set CRAWLAB_API_TOKEN or its _FILE")
    command = program_command(python, settings_file)
    with httpx.Client(
        base_url=base_url.rstrip("/") + "/",
        headers={"Authorization": token},
        trust_env=False,
        timeout=15,
        follow_redirects=False,
        transport=transport,
    ) as client:

        def call(method, path, payload=None):
            response = client.request(method, path, json=payload)
            if response.status_code != 200:
                raise RuntimeError("Crawlab returned HTTP " + str(response.status_code))
            value = response.json()
            if value.get("error") or value.get("status") == "error":
                raise RuntimeError("Crawlab returned an API error")
            return value.get("data")

        existing = []
        for page in range(1, 101):
            rows = call("GET", f"spiders?page={page}&size=100")
            if not isinstance(rows, list):
                raise ValueError("Unexpected Spider list shape")
            existing.extend(rows)
            if len(rows) < 100:
                break
        else:
            raise ValueError("Too many programs for this small setup helper")
        result = {}
        for kind in ("capture", "test"):
            name = "proxypool-" + kind
            matches = [x for x in existing if x.get("name") == name]
            if len(matches) > 1:
                raise ValueError(
                    "Duplicate Proxypool program names; select IDs in Crawlab"
                )
            if matches:
                spider = matches[0]
                if spider.get("description") != "server-craw-cluster/proxypool":
                    raise ValueError("Existing program belongs to another application")
            else:
                spider = call(
                    "POST",
                    "spiders",
                    {
                        "name": name,
                        "description": "server-craw-cluster/proxypool",
                        "col_name": name.replace("-", "_"),
                        "type": "customized",
                        "cmd": command,
                    },
                )
            identifier = spider["_id"]
            if spider.get("cmd") != command:
                # Native PUT saves the whole document; retain existing program fields.
                current = call("GET", "spiders/" + identifier)
                current["cmd"] = command
                call("PUT", "spiders/" + identifier, current)
            sync_program(call, identifier)
            result["capture_spider_id" if kind == "capture" else "tester_spider_id"] = (
                identifier
            )
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--python", default="python", help="Interpreter available on generic nodes"
    )
    parser.add_argument(
        "--settings-file", help="Node-private JSON path, never source-synced"
    )
    args = parser.parse_args()
    settings = Settings.load(args.config)
    print(
        json.dumps(
            register(
                settings.crawlab_url,
                secret("CRAWLAB_API_TOKEN"),
                python=args.python,
                settings_file=args.settings_file,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
