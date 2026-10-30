"""Check API build inputs and complete source delivery without invoking Docker."""

from pathlib import Path
import shlex
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integrations.crawlab.program import program_files  # noqa: E402 - direct script entry resolves its repository root


def main():
    copied = []
    for line in (
        (ROOT / "deploy/proxypool/Dockerfile").read_text(encoding="utf-8").splitlines()
    ):
        if line.startswith("COPY "):
            for source in shlex.split(line)[1:-1]:
                if not (ROOT / source).exists():
                    raise ValueError("Missing build input: " + source)
                copied.append(Path(source))
    required = [
        "apps/proxypool/web/index.html",
        "tools/register_crawlab.py",
        "tools/configure_admin.py",
        "apps/proxypool/service/auth.py",
        "integrations/crawlab/program.py",
        "requirements/tasks.txt",
        "apps/proxypool/task_entry.py",
    ]
    for value in required:
        if not any(
            Path(value) == source or Path(value).is_relative_to(source)
            for source in copied
        ):
            raise ValueError("API runtime asset not copied: " + value)
    runtime = ROOT / "deploy/crawlab/worker-runtime/Dockerfile"
    instructions = [
        line.strip()
        for line in runtime.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    copies = [
        shlex.split(line)[1:-1] for line in instructions if line.startswith("COPY ")
    ]
    if copies != [["requirements/tasks.txt"]]:
        raise ValueError("Shared Worker image must copy only its dependency manifest")
    if any(
        line.startswith(("CMD ", "ENTRYPOINT ", "WORKDIR ", "ENV "))
        for line in instructions
    ):
        raise ValueError("Shared image must preserve the upstream startup environment")
    files = program_files()
    assert "proxypool_task.py" in files and "requirements.txt" in files
    assert not any("service/" in name or "web/" in name for name in files)
    print(
        f"API/shared-runtime inputs and {len(files)} task files present; Docker execution remains unverified"
    )


if __name__ == "__main__":
    main()
