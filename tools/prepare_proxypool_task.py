"""User-run: create one private task configuration, without managing any container."""

import argparse
import json
import os
from pathlib import Path
import re


def prepare(
    root=Path("/"), *, node="home-worker-01", callback_url="", max_concurrency=20
):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", node):
        raise ValueError("Invalid node directory name")
    from urllib.parse import urlsplit

    url = urlsplit(callback_url)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError(
            "Use the accepted HTTPS callback base URL, without credentials or query"
        )
    if type(max_concurrency) is not int or not 1 <= max_concurrency <= 1000:
        raise ValueError("Invalid node request concurrency")
    root = Path(root).resolve()
    if root == Path("/") and os.name == "posix" and os.geteuid() != 0:
        raise PermissionError("Run through sudo")
    directory = root / "srv/persistent/secrets/crawlab-tasks" / node
    source = root / "srv/persistent/secrets/proxypool/app.env"
    target = directory / "proxypool.json"
    for path in (source, target):
        if not path.resolve().is_relative_to(root):
            raise ValueError("Path escapes the selected root")
        for ancestor in (path, *path.parents):
            if ancestor == root:
                break
            if ancestor.is_symlink():
                raise ValueError("Refusing symlinked secret path")
    values = dict(
        line.split("=", 1)
        for line in source.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    key = values.get("PROXYPOOL_API_KEY", "")
    if len(key) < 16:
        raise ValueError("Existing API key is missing or too short")
    payload = {
        "m05_url": callback_url.rstrip("/"),
        "api_key": key,
        "max_concurrency": max_concurrency,
    }
    if target.exists():
        if json.loads(target.read_text(encoding="utf-8")) != payload:
            raise ValueError("Existing task settings differ; not overwritten")
        return {"created": False, "path": str(target), "secrets_printed": False}
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.mkdir(exist_ok=True)
    os.chmod(directory.parent, 0o700)
    os.chmod(directory, 0o700)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, indent=2)
        stream.write("\n")
    return {"created": True, "path": str(target), "secrets_printed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--callback-url", required=True)
    parser.add_argument("--node", default="home-worker-01")
    parser.add_argument("--max-concurrency", type=int, default=20)
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(
                node=args.node,
                callback_url=args.callback_url,
                max_concurrency=args.max_concurrency,
            )
        )
    )


if __name__ == "__main__":
    main()
