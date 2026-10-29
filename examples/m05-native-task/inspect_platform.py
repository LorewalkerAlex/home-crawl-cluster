"""Inspect a pinned Crawlab API, keeping one login in memory until exit.

No Spider, Task, database, file, Docker or server configuration mutations.
Only POST /login establishes a session; subsequent platform requests are GET.
The PC read-only path was accepted from the user's 2026-10-28 report.
"""

import getpass
import argparse
import json
import re
import sys

from native_task import JsonClient, TaskError


# Derived from core 25ed70356f13/constants/node.go, not translated UI labels.
NODE_STATUSES = {"u", "r", "on", "off"}
SAFE_FIELDS = ("_id", "name", "is_master", "enabled", "status", "available_runners", "max_runners")


def safe_node(node):
    result = {}
    for key in SAFE_FIELDS:
        value = node.get(key)
        if key in ("is_master", "enabled"):
            result[key] = value if type(value) is bool else None
        elif key in ("available_runners", "max_runners"):
            result[key] = value if type(value) is int and value >= 0 else None
        elif key == "_id":
            result[key] = value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{24}", value) else None
        else:
            result[key] = value if isinstance(value, str) and len(value) <= 96 else None
    return result


def inspect_nodes(api, worker_name="home-worker-01"):
    """Return observed safe fields and a reason before attempting any trial."""
    response = api.request("GET", "/nodes?page=1&size=100")
    raw = response.get("data")
    result = {"readiness": "incompatible", "reason": "invalid_node_list", "nodes": [], "selected_node": None}
    if not isinstance(raw, list) or len(raw) > 100 or any(not isinstance(n, dict) for n in raw):
        return result
    nodes = [safe_node(node) for node in raw]
    result["nodes"] = nodes
    total = response.get("total")
    if type(total) is not int or total != len(nodes):
        result["reason"] = "node_list_incomplete_or_invalid_total"
        return result
    if any(any(node[key] is None for key in SAFE_FIELDS) or node["status"] not in NODE_STATUSES for node in nodes):
        result["reason"] = "unexpected_node_field_or_status"
        return result
    if any(n["available_runners"] > n["max_runners"] for n in nodes):
        result["reason"] = "inconsistent_runner_counts"
        return result
    candidates = [n for n in nodes if n["name"] == worker_name and n["is_master"] is False]
    if len(candidates) != 1:
        result.update(readiness="unavailable", reason="expected_one_named_worker")
        return result
    worker = candidates[0]
    result["selected_node"] = worker["_id"]
    if worker["enabled"] is not True or worker["status"] != "on":
        result.update(readiness="unavailable", reason="worker_disabled_or_not_online")
    elif worker["max_runners"] == 0:
        result.update(readiness="unavailable", reason="no_runner_slots_configured")
    elif worker["available_runners"] == 0:
        result.update(readiness="busy", reason="wait_for_available_runner")
    else:
        result.update(readiness="ready", reason="online_enabled_worker_has_capacity")
    return result


def inspect_platform(api):
    # This core's GetVersion returns a constant v0.6.2; it is not an image gate.
    version = api.request("GET", "/version").get("data")
    observed = version if isinstance(version, str) and re.fullmatch(r"v?\d+\.\d+\.\d+", version) else None
    return {"api_version_observed": observed, "version_used_as_gate": False,
            "scope": "node_selection_only", **inspect_nodes(api)}


def authenticate(api, *, read=input, read_password=getpass.getpass):
    """Establish one in-memory login; never persist credentials or return a token."""
    username = read("Crawlab username [admin]: ").strip() or "admin"
    password = read_password("Crawlab password (hidden): ")
    try:
        login = api.request("POST", "/login", {"username": username, "password": password})
    finally:
        del password
    token = login.get("data")
    if not isinstance(token, str) or not token:
        raise TaskError("Login did not return a token")
    api.token = token


def session(api, *, read=input, read_password=getpass.getpass, output=print):
    """One login, explicit refresh, no automatic polling or trial creation."""
    try:
        authenticate(api, read=read, read_password=read_password)
        while True:
            try:
                report = inspect_platform(api)
                output(json.dumps({"phase": "inspect", **report}, ensure_ascii=True))
            except TaskError as error:
                # No raw response, query, credentials or token are displayed.
                output(json.dumps({"phase": "inspect", "error": str(error)}))
            while True:
                action = read("Enter check to refresh, or quit to exit [quit]: ").strip().lower()
                if action in ("", "quit"):
                    return
                if action == "check":
                    break
                output(json.dumps({"error": "allowed_commands_are_check_or_quit"}))
    except (EOFError, KeyboardInterrupt):
        return
    finally:
        api.token = None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base-url", default="http://127.0.0.1:13003/api",
                        help="Loopback HTTP or the explicitly selected HTTPS API origin")
    args = parser.parse_args(argv)
    try:
        if not sys.stdin.isatty():
            raise TaskError("An interactive terminal is required")
        api = JsonClient(args.api_base_url, timeout=15, allow_https=True)
        # Confirm DNS/TLS/HTTP before asking for any password.
        api.request("GET", "/version")
        print(json.dumps({"phase": "connection", "check": "PASS"}), flush=True)
    except TaskError as error:
        print(json.dumps({"phase": "connection", "check": "FAIL", "error": str(error)}))
        return 1
    try:
        session(api)
        return 0
    except TaskError as error:
        print(json.dumps({"phase": "login", "error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
