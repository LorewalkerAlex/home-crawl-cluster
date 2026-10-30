"""Explicit source-only Crawlab program delivery using its native file API."""

from pathlib import Path
import shlex
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
FILES = (
    "apps/proxypool/task_batches.py",
    "apps/proxypool/task_main.py",
    "apps/proxypool/task_settings.py",
    "apps/proxypool/contracts.py",
    "apps/proxypool/tester/execute.py",
    "apps/proxypool/crawler/execute.py",
    "apps/proxypool/crawler/proxy.py",
    "apps/proxypool/crawler/registry.py",
    "apps/proxypool/crawler/spiders/base.py",
    "apps/proxypool/crawler/spiders/ip89.py",
    "apps/proxypool/crawler/spiders/ipscdn.py",
    "apps/proxypool/crawler/spiders/ipfreeonly.py",
    "apps/proxypool/crawler/spiders/ip3366.py",
    "apps/proxypool/crawler/spiders/example_table.py",
    "apps/proxypool/crawler/spiders/proxyscrape.py",
    "apps/proxypool/crawler/spiders/proxy_list_download.py",
)


def program_files():
    files = {name: (ROOT / name).read_text(encoding="utf-8") for name in FILES}
    files["proxypool_task.py"] = (ROOT / "apps/proxypool/task_entry.py").read_text(encoding="utf-8")
    files["requirements.txt"] = (ROOT / "requirements/tasks.txt").read_text(encoding="utf-8")
    for folder in (
        "apps",
        "apps/proxypool",
        "apps/proxypool/tester",
        "apps/proxypool/crawler",
        "apps/proxypool/crawler/spiders",
    ):
        files[folder + "/__init__.py"] = "# Delivered Proxypool task package.\n"
    return files


def program_command(python="python", settings_file=None):
    if not python or any(c in python for c in "\r\n\x00"):
        raise ValueError("Invalid interpreter command")
    command = shlex.quote(python) + " -B -u proxypool_task.py"
    if settings_file:
        command += " --settings " + shlex.quote(str(settings_file))
    return command


def sync_program(call, spider_id):
    route = "spiders/" + spider_id + "/files/"
    for path, content in program_files().items():
        call("POST", route + "save", {"path": "/" + path, "data": content})
        if call("GET", route + "get?path=" + quote("/" + path, safe="/")) != content:
            raise ValueError("Program file verification failed: " + path)
