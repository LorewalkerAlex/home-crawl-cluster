"""User-run once: register two programs and sync their small bootstrap file."""

import argparse
import json
from pathlib import Path
import sys
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apps.proxypool.service.config import Settings, secret


def register(base_url, token, *, transport=None):
    if not token:
        raise ValueError("Set CRAWLAB_API_TOKEN or its _FILE")
    content = (ROOT / "deploy/proxypool/proxypool_task.py").read_text(encoding="utf-8")
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
                raise ValueError("Duplicate Proxypool program names; select IDs in Crawlab")
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
                        "cmd": "python -B -u proxypool_task.py",
                    },
                )
            identifier = spider["_id"]
            route = "spiders/" + identifier + "/files/"
            call("POST", route + "save", {"path": "/proxypool_task.py", "data": content})
            if call("GET", route + "get?path=/proxypool_task.py") != content:
                raise ValueError("Bootstrap source verification failed")
            result["capture_spider_id" if kind == "capture" else "tester_spider_id"] = identifier
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    settings = Settings.load(args.config)
    print(json.dumps(register(settings.crawlab_url, secret("CRAWLAB_API_TOKEN")), indent=2))


if __name__ == "__main__":
    main()
