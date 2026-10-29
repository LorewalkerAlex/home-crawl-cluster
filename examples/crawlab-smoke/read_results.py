"""Read and validate an existing platform_smoke task through Crawlab 0.6.3 API."""

import getpass
import json
import re
import sys
import urllib.error
import urllib.request


BASE_URL = "http://127.0.0.1:13003/api"
MAX_RESPONSE_BYTES = 1024 * 1024


class CheckFailed(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(opener, path, token=None, payload=None):
    headers = {"Accept": "application/json"}
    if token is not None:
        # This pinned version expects the raw token, without a Bearer prefix.
        headers["Authorization"] = token
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(BASE_URL + path, data=data, headers=headers)
    try:
        with opener.open(request, timeout=15) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise CheckFailed("API returned HTTP {}; response body omitted".format(error.code)) from None
    except (urllib.error.URLError, OSError):
        raise CheckFailed("Cannot reach the local Crawlab API; check Master availability") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise CheckFailed("API response exceeded the 1 MiB diagnostic limit")
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        raise CheckFailed("API response is not valid JSON") from None
    if not isinstance(body, dict) or body.get("error") or body.get("message") == "error":
        raise CheckFailed("API returned an error or unexpected structure; body omitted")
    return body


def verify_task_results(opener, token, task_id):
    task = request_json(opener, "/tasks/" + task_id, token).get("data")
    if not isinstance(task, dict) or task.get("status") != "finished":
        raise CheckFailed("Selected task is missing or is not finished; do not rerun it yet")
    records = []
    page_sizes = []
    # Two populated pages and one empty page prove the small pagination boundary.
    for page, expected_size in ((1, 2), (2, 1), (3, 0)):
        body = request_json(opener, "/tasks/{}/data?page={}&size=2".format(task_id, page), token)
        data = body.get("data")
        if data is None and expected_size == 0:
            data = []
        if body.get("total") != 3 or not isinstance(data, list) or len(data) != expected_size:
            raise CheckFailed("Unexpected total or page size on page {}; keep the task for inspection".format(page))
        if any(not isinstance(record, dict) for record in data):
            raise CheckFailed("Unexpected result record structure")
        records.extend(data)
        page_sizes.append(len(data))
    if any(record.get("kind") != "platform_smoke" or record.get("message") != "hello_crawlab"
           for record in records):
        raise CheckFailed("Records do not match the synthetic smoke program")
    sequences = [record.get("sequence") for record in records]
    if any(type(value) is not int for value in sequences) or sorted(sequences) != [1, 2, 3]:
        raise CheckFailed("Result sequences are missing, duplicated or invalid")
    markers = [record.get("run_marker") for record in records]
    if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value) for value in markers):
        raise CheckFailed("Invalid smoke run marker")
    if len(set(markers)) != 1:
        raise CheckFailed("Results contain different smoke run markers")
    return {"check": "PASS", "task_status": "finished", "total": 3,
            "page_sizes": page_sizes, "sequences": sorted(sequences), "same_run_marker": True}


def main():
    if not sys.stdin.isatty():
        raise CheckFailed("Run this saved file in an interactive server terminal")
    task_id = input("Existing smoke Task ID (24 hex characters): ").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{24}", task_id):
        raise CheckFailed("Invalid Task ID; copy it from the existing task Overview")
    username = input("Crawlab username [admin]: ").strip() or "admin"
    password = getpass.getpass("Crawlab password (hidden): ")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    login = request_json(opener, "/login", payload={"username": username, "password": password})
    del password
    token = login.get("data")
    if not isinstance(token, str) or not token:
        raise CheckFailed("Login did not return a token; response body omitted")
    print(json.dumps(verify_task_results(opener, token, task_id), ensure_ascii=True))


if __name__ == "__main__":
    try:
        main()
    except CheckFailed as error:
        print("CHECK_FAILED: " + str(error), file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print("Check cancelled", file=sys.stderr)
        sys.exit(1)
