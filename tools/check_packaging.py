"""Check runtime repository assets; does not emulate Docker/Compose."""

from pathlib import Path
import shlex

ROOT = Path(__file__).resolve().parents[1]


def main():
    for filename in ("Dockerfile", "Dockerfile.worker"):
        path = ROOT / "deploy/proxypool" / filename
        copied = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("COPY "):
                args = shlex.split(line)[1:]
                for source in args[:-1]:
                    if not (ROOT / source).exists():
                        raise ValueError("Missing build input: " + source)
                    copied.append(Path(source))
        required = ["apps/proxypool/worker.py", "apps/proxypool/service/models.py"]
        if filename == "Dockerfile":
            required += [
                "apps/proxypool/web/index.html",
                "tools/register_crawlab.py",
                "deploy/proxypool/proxypool_task.py",
            ]
        for value in required:
            if not any(
                Path(value) == source or Path(value).is_relative_to(source) for source in copied
            ):
                raise ValueError("Runtime asset not copied into " + filename + ": " + value)
    print("Runtime build inputs present; Docker/Compose execution is still unverified")


if __name__ == "__main__":
    main()
