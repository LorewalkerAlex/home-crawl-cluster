"""Build an allowlisted, deterministic zipapp locally; never deploys it."""

import argparse
import ast
import hashlib
from pathlib import Path
import zipfile


def build(root, output):
    base = root / "examples/m05-native-task"
    files = {"__main__.py": base / "server_poc.py", "native_task.py": base / "native_task.py",
             "inspect_platform.py": base / "inspect_platform.py",
             "worker.py": base / "worker.py", "cleanup_files.py": base / "cleanup_files.py",
             "cleanup_native.js": base / "cleanup_native.js", "apps/__init__.py": None,
             "apps/proxypool/__init__.py": None}
    for name in ("__init__", "contracts", "storage", "quality", "http_transport", "http_api"):
        files["apps/proxypool/service/" + name + ".py"] = root / "apps/proxypool/service" / (name + ".py")
    contents = {}
    for name, source in files.items():
        content = source.read_text(encoding="utf-8") if source else ""
        if name.endswith(".py"):
            ast.parse(content, filename=name, feature_version=(3, 10))
        contents[name] = content.encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_STORED) as archive:
        for name, content in contents.items():
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            archive.writestr(info, content)
    return hashlib.sha256(output.read_bytes()).hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print("SHA256:", build(Path(__file__).resolve().parents[2], args.output))
