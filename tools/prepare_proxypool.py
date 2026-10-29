"""User-run deployment preparation. No Docker calls, downloads or service changes."""

import argparse
import json
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apps.proxypool.service.config import Settings
from dataclasses import asdict


def prepare(root=Path("/"), *, apply=False):
    root = Path(root).resolve()
    app = root / "srv/apps/proxypool"
    private = root / "srv/persistent/secrets/proxypool"
    data = root / "srv/persistent/data/proxypool"
    for destination in (app, private, data):
        assert destination.is_relative_to(root)
        for parent in [destination, *destination.parents]:
            if parent == root:
                break
            if parent.is_symlink():
                raise ValueError("Refusing symlinked preparation path")
    if not apply:
        return {
            "prepared": False,
            "directories": [str(x) for x in (app, private, data)],
        }
    if os.name == "posix" and root == Path("/") and os.geteuid() != 0:
        raise PermissionError("Run preparation with sudo")
    for destination in (app, private, data):
        destination.mkdir(parents=True, exist_ok=True)
    os.chmod(private, 0o700)
    os.chmod(data, 0o700)
    if os.name == "posix" and root == Path("/"):
        if os.geteuid() != 0:
            raise PermissionError("Run preparation with sudo")
        os.chown(data, 10001, 10001)
    env = private / "app.env"
    key = None
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("PROXYPOOL_API_KEY="):
                key = line.split("=", 1)[1]
        if not key:
            raise ValueError("Existing app.env lacks an API key; not overwritten")
    else:
        key = secrets.token_urlsafe(32)
    config = asdict(
        Settings(
            database="/data/proxypool.sqlite3",
            host="0.0.0.0",
            source_registry_file="/config/source-registry.json",
        )
    )
    files = {
        env: ("PROXYPOOL_API_KEY=" + key + "\nCRAWLAB_API_TOKEN=\n", 0o600),
        app / "config.json": (json.dumps(config, indent=2) + "\n", 0o644),
        app / "source-registry.json": ("{}\n", 0o644),
    }
    created = []
    for path, (content, mode) in files.items():
        if path.exists():
            continue
        with path.open("x", encoding="utf-8", newline="\n") as file:
            file.write(content)
        os.chmod(path, mode)
        created.append(str(path))
    return {
        "prepared": True,
        "created": created,
        "secrets_printed": False,
        "next": "Configure app.env Crawlab token and config.json API URL/program IDs. Generic node runtime and private callback settings belong to Crawlab administration. Keep automatic publishing off.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if os.name == "nt" and args.apply and args.root == Path("/"):
        parser.error("Use an explicit temporary --root for local preparation tests")
    print(json.dumps(prepare(args.root, apply=args.apply), ensure_ascii=True))


if __name__ == "__main__":
    main()
