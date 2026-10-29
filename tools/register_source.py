"""Explicit user-run source registration: AST contract check, native sync, registry update."""

import argparse
import ast
import http.client
import json
from pathlib import Path
import re
import ssl
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def native_client(base_url, token):
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Expected a Crawlab HTTP(S) base URL without credentials")

    def call(method, path, payload=None):
        headers = {"Authorization": token, "Accept": "application/json"}
        body = (
            None if payload is None else json.dumps(payload, ensure_ascii=True).encode()
        )
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection = (
            http.client.HTTPSConnection(
                parsed.hostname,
                parsed.port or 443,
                timeout=15,
                context=ssl.create_default_context(),
            )
            if parsed.scheme == "https"
            else http.client.HTTPConnection(
                parsed.hostname, parsed.port or 80, timeout=15
            )
        )
        try:
            connection.request(
                method, parsed.path.rstrip("/") + "/" + path, body=body, headers=headers
            )
            response = connection.getresponse()
            raw = response.read(1024 * 1024 + 1)
            if response.status != 200 or len(raw) > 1024 * 1024:
                raise RuntimeError("Crawlab request failed; response body omitted")
            result = json.loads(raw)
            if result.get("error") or result.get("status") == "error":
                raise RuntimeError("Crawlab API error")
            return result.get("data")
        finally:
            connection.close()

    return call


def validate_source(path):
    path = Path(path)
    if path.stat().st_size > 65536:
        raise ValueError("Source script exceeds 64 KiB")
    content = path.read_text(encoding="utf-8")
    tree = ast.parse(content, filename=path.name, feature_version=(3, 10))
    matching = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "ProxySourceSpider"
        and any(
            isinstance(base, ast.Name) and base.id == "SourceSpider"
            for base in node.bases
        )
    ]
    if len(matching) != 1:
        raise ValueError("Define one ProxySourceSpider(SourceSpider) class")
    return content


def register_source(call, registry, source, *, capture_spider_id, key, name, url):
    if not re.fullmatch("[a-z0-9_]{1,64}", key):
        raise ValueError("Invalid source key")
    if not name or len(name) > 80 or urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("Invalid source metadata")
    content = validate_source(source)
    registry = Path(registry)
    current = (
        json.loads(registry.read_text(encoding="utf-8")) if registry.exists() else {}
    )
    if not isinstance(current, dict) or len(current) > 64:
        raise ValueError("Invalid registry")
    entry = current.get(key)
    if entry is None and len(current) >= 64:
        raise ValueError("Source registry limit reached")
    identifier = capture_spider_id
    if not re.fullmatch("[0-9a-f]{24}", identifier):
        raise ValueError("Expected the registered capture program ID")
    program = call("GET", "spiders/" + identifier)
    if (
        not isinstance(program, dict)
        or program.get("description") != "server-craw-cluster/proxypool"
        or program.get("name") != "proxypool-capture"
    ):
        raise ValueError("Capture program is not owned by this Proxypool registration")
    filename = "sources/" + key + ".py"
    route = "spiders/" + identifier + "/files/"
    call("POST", route + "save", {"path": "/" + filename, "data": content})
    if call("GET", route + "get?path=/" + filename) != content:
        raise ValueError("Registered source round-trip failed")
    current[key] = {"name": name, "example_url": url}
    if len(current) > 64:
        raise ValueError("Source registry limit reached")
    # Preserve the inode for the existing read-only bind mount. No server imports.
    with registry.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(current, file, ensure_ascii=True, indent=2)
        file.write("\n")
    return {"registered": key, "spider_id": identifier, "restart_required": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crawlab-url", required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--capture-spider-id", required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    values = dict(
        line.split("=", 1)
        for line in args.env_file.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    token = values.get("CRAWLAB_API_TOKEN", "")
    if not token:
        parser.error("Private env file lacks CRAWLAB_API_TOKEN")
    result = register_source(
        native_client(args.crawlab_url, token),
        args.registry,
        args.source,
        capture_spider_id=args.capture_spider_id,
        key=args.key,
        name=args.name,
        url=args.url,
    )
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
