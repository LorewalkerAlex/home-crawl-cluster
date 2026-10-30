"""Private node configuration copies only the business key and never replaces secrets."""

import json

import pytest

from tools.prepare_proxypool_task import prepare


def test_private_task_config_is_idempotent_and_does_not_expose_secrets(tmp_path):
    private = tmp_path / "srv/persistent/secrets/proxypool"
    private.mkdir(parents=True)
    key = "synthetic-business-key-for-test-only"
    token = "synthetic-platform-token-not-for-worker"
    env = private / "app.env"
    env.write_text("PROXYPOOL_API_KEY=" + key + "\nCRAWLAB_API_TOKEN=" + token + "\n")
    result = prepare(
        tmp_path, callback_url="https://example.invalid:4129", max_concurrency=3
    )
    assert result["created"] is True and key not in json.dumps(result)
    path = (
        tmp_path / "srv/persistent/secrets/crawlab-tasks/home-worker-01/proxypool.json"
    )
    data = json.loads(path.read_text())
    assert data["api_key"] == key and data["max_concurrency"] == 3
    assert token not in path.read_text()
    assert (
        prepare(
            tmp_path, callback_url="https://example.invalid:4129", max_concurrency=3
        )["created"]
        is False
    )
    with pytest.raises(ValueError, match="not overwritten"):
        prepare(tmp_path, callback_url="https://different.invalid:4129")
    assert json.loads(path.read_text()) == data


def test_invalid_node_and_credential_urls_create_no_files(tmp_path):
    with pytest.raises(ValueError, match="node"):
        prepare(tmp_path, node="../other", callback_url="https://example.invalid")
    with pytest.raises(ValueError, match="HTTPS"):
        prepare(tmp_path, callback_url="https://user:secret@example.invalid")
    assert not (tmp_path / "srv").exists()
