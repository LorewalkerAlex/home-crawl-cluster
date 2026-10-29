"""Read-only deployed API probe: no tasks or data are created."""

import argparse
import json
from pathlib import Path
import sys
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apps.proxypool.service.config import secret


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:13004")
    args = parser.parse_args()
    key = secret("PROXYPOOL_API_KEY")
    if not key:
        parser.error("Configure PROXYPOOL_API_KEY or _FILE")
    with httpx.Client(
        base_url=args.base_url,
        headers={"Authorization": "Bearer " + key},
        trust_env=False,
        timeout=10,
        follow_redirects=False,
    ) as client:
        values = {}
        for path in ("/healthz", "/v1/stats", "/v1/targets", "/v1/sources"):
            response = client.get(path)
            response.raise_for_status()
            values[path] = response.json()
    assert values["/healthz"]["ok"] is True
    print(
        json.dumps(
            {
                "check": "PASS",
                "read_only": True,
                "proxy_count": values["/v1/stats"]["proxy_count"],
                "targets": len(values["/v1/targets"]["items"]),
                "sources": len(values["/v1/sources"]["items"]),
            }
        )
    )


if __name__ == "__main__":
    main()
