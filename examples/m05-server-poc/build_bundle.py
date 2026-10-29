"""Build the reviewed receive-only POC zipapp; never connects to a server."""

import argparse
import ast
import hashlib
from pathlib import Path
import zipfile


def build(root, output):
    files = {
        "__main__.py": root / "examples/m05-server-poc/server_poc.py",
        "apps/__init__.py": None,
        "apps/proxypool/__init__.py": None,
        "apps/proxypool/service/__init__.py": root / "apps/proxypool/service/__init__.py",
        "apps/proxypool/service/contracts.py": root / "apps/proxypool/service/contracts.py",
        "apps/proxypool/service/receiver.py": root / "apps/proxypool/service/receiver.py",
        "apps/proxypool/service/http_transport.py": root / "apps/proxypool/service/http_transport.py",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    # Refuse accidental replacement of an upload that might already be under review.
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_STORED) as archive:
        for name, source in files.items():
            code = source.read_text(encoding="utf-8") if source else ""
            ast.parse(code, filename=name, feature_version=(3, 8))
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            archive.writestr(info, code.encode("utf-8"))
    return hashlib.sha256(output.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    print("SHA256:", build(root, args.output))


if __name__ == "__main__":
    main()
