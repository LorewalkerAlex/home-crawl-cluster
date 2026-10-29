"""Run offline tests with task-owned temporary files; never invokes Docker."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", help="Optional Node executable for historical cleanup fixtures")
    parser.add_argument(
        "--browser", help="Optional installed Chromium/Edge executable; no download"
    )
    args = parser.parse_args()
    subprocess.run(
        [sys.executable, "-B", str(ROOT / "tools/check_packaging.py")], cwd=ROOT, check=True
    )
    artifacts = ROOT / ".local/check-results"
    artifacts.mkdir(parents=True, exist_ok=True)
    environment = dict(
        os.environ,
        PYTHONUTF8="1",
        PYTHONDONTWRITEBYTECODE="1",
        PROXYPOOL_SCREENSHOT=str(artifacts / "frontend.png"),
    )
    if args.node:
        environment["PATH"] = (
            str(Path(args.node).resolve().parent) + os.pathsep + environment.get("PATH", "")
        )
    if args.browser:
        environment["PROXYPOOL_BROWSER"] = str(Path(args.browser).resolve())
    with tempfile.TemporaryDirectory(prefix="run-", dir=artifacts) as folder:
        temporary = Path(folder).resolve()
        assert temporary.is_relative_to(artifacts.resolve())
        environment.update(TEMP=str(temporary), TMP=str(temporary))
        command = [
            sys.executable,
            "-B",
            "-W",
            "error::ResourceWarning",
            "-m",
            "pytest",
            "tests",
            "-q",
            "--tb=short",
            "--basetemp",
            str(temporary / "pytest"),
            "--junitxml",
            str(artifacts / "tests.xml"),
        ]
        result = subprocess.run(command, cwd=ROOT, env=environment)
    if (artifacts / "tests.xml").exists():
        root = ET.parse(artifacts / "tests.xml").getroot()
        totals = {
            name: sum(int(x.get(name, 0)) for x in root.findall("testsuite"))
            for name in ("tests", "failures", "errors", "skipped")
        }
        print("Test summary:", totals, flush=True)
    print("Temporary test data removed; persistent evidence:", artifacts, flush=True)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
