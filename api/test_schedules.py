"""Unit tests for the cron scheduler (schedules.py + server /schedules routes).

These never touch a real crontab, cron daemon, the `bob` binary, or the network:
`install_crontab` is stubbed, the registry is redirected to a tmp file, and
`server._stream_exec` is mocked when a run is triggered. Fast and offline.
"""
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import schedules
import server


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """Point the schedule registry at a tmp file and stub crontab installation."""
    path = tmp_path / "schedules.json"
    monkeypatch.setattr(schedules, "SCHEDULES_FILE", str(path))
    installed = []
    monkeypatch.setattr(
        schedules, "install_crontab", lambda scheds: installed.append(list(scheds)) or True
    )
    return path, installed


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setenv("BOBSHELL_API_KEY", "test-key")
    return TestClient(server.app)


# --------------------------------------------------------------------------- #
# Cron validation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "expr",
    ["0 9 * * *", "*/15 * * * *", "0 8 * * 1-5", "0,30 * * * *", "15 14 1 * *"],
)
def test_valid_cron_accepts(expr):
    assert schedules.valid_cron(expr)


@pytest.mark.parametrize(
    "expr",
    ["", "0 9 * *", "@daily", "0 9 * * mon", "not a cron", "60 9 * * * *"],
)
def test_valid_cron_rejects(expr):
    assert not schedules.valid_cron(expr)


# --------------------------------------------------------------------------- #
# Crontab generation
# --------------------------------------------------------------------------- #
def test_crontab_body_renders_lines_and_curl():
    body = schedules.crontab_body(
        [{"id": "abc123", "name": "nightly", "cron": "0 0 * * *", "enabled": True}]
    )
    assert "PATH=" in body
    assert "# [abc123] nightly" in body
    assert "0 0 * * * curl -fsS -X POST" in body
    assert "/schedules/abc123/run" in body
    # Each acknowledgment ends with a newline so the log stays line-delimited.
    assert "; echo >>" in body
    assert body.endswith("\n")


def test_crontab_body_skips_disabled():
    body = schedules.crontab_body(
        [{"id": "x", "cron": "0 0 * * *", "enabled": False}]
    )
    assert "/schedules/x/run" not in body


# --------------------------------------------------------------------------- #
# CRUD (module level)
# --------------------------------------------------------------------------- #
def test_add_persists_and_installs(registry):
    _, installed = registry
    s = schedules.add(cron="0 9 * * *", prompt="do the thing", name="daily")
    assert s["id"] and s["cron"] == "0 9 * * *" and s["enabled"] is True
    assert schedules.get(s["id"])["prompt"] == "do the thing"
    assert len(schedules.load()) == 1
    assert installed, "crontab should have been (re)installed on add"


def test_add_rejects_bad_cron(registry):
    with pytest.raises(schedules.ScheduleError):
        schedules.add(cron="@daily", prompt="x")


def test_add_rejects_empty_prompt(registry):
    with pytest.raises(schedules.ScheduleError):
        schedules.add(cron="0 9 * * *", prompt="   ")


def test_remove(registry):
    s = schedules.add(cron="0 9 * * *", prompt="x")
    assert schedules.remove(s["id"]) is True
    assert schedules.load() == []
    assert schedules.remove("nope") is False


def test_mark_run_records_last_status(registry):
    s = schedules.add(cron="0 9 * * *", prompt="x")
    schedules.mark_run(s["id"], run_id="deadbeef", status="completed")
    got = schedules.get(s["id"])
    assert got["last_run_id"] == "deadbeef"
    assert got["last_status"] == "completed"
    assert got["last_run"] is not None


def test_log_outcome_appends_final_status(tmp_path, monkeypatch):
    import json

    log = tmp_path / "cron.log"
    monkeypatch.setattr(schedules, "CRON_LOG", str(log))
    schedules.log_outcome(
        "abc123", run_id="deadbeef", status="failed", name="nightly"
    )
    lines = log.read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["event"] == "outcome"
    assert entry["schedule_id"] == "abc123"
    assert entry["run_id"] == "deadbeef"
    assert entry["status"] == "failed"
    assert entry["name"] == "nightly"
    assert entry["ts"]


def test_log_outcome_never_raises_on_unwritable_log(monkeypatch):
    # A bad path must not crash the recording thread.
    monkeypatch.setattr(schedules, "CRON_LOG", "/nonexistent-dir/cron.log")
    schedules.log_outcome("x", run_id="y", status="completed")  # no raise


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
def test_create_schedule_endpoint(client):
    r = client.post("/schedules", json={"cron": "0 9 * * *", "prompt": "hi", "name": "n"})
    assert r.status_code == 201
    body = r.json()
    assert body["cron"] == "0 9 * * *" and body["name"] == "n"
    assert "id" in body


def test_create_schedule_bad_cron_returns_400(client):
    r = client.post("/schedules", json={"cron": "@daily", "prompt": "hi"})
    assert r.status_code == 400


def test_list_and_get_schedule(client):
    created = client.post("/schedules", json={"cron": "0 9 * * *", "prompt": "hi"}).json()
    sid = created["id"]

    r = client.get("/schedules")
    assert r.status_code == 200
    assert any(s["id"] == sid for s in r.json()["schedules"])

    r = client.get(f"/schedules/{sid}")
    assert r.status_code == 200 and r.json()["id"] == sid

    assert client.get("/schedules/missing").status_code == 404


def test_delete_schedule(client):
    sid = client.post("/schedules", json={"cron": "0 9 * * *", "prompt": "hi"}).json()["id"]
    assert client.delete(f"/schedules/{sid}").status_code == 200
    assert client.get(f"/schedules/{sid}").status_code == 404
    assert client.delete("/schedules/missing").status_code == 404


def test_run_schedule_triggers_a_run(client):
    sid = client.post("/schedules", json={"cron": "0 9 * * *", "prompt": "hi"}).json()["id"]
    with patch.object(server, "_stream_exec", return_value=("ok", 0, False)):
        r = client.post(f"/schedules/{sid}/run")
        assert r.status_code == 202
        run_id = r.json()["id"]
        # The run is registered and inspectable via /jobs.
        assert client.get(f"/jobs/{run_id}").status_code == 200


def test_run_missing_schedule_returns_404(client):
    r = client.post("/schedules/nope/run")
    assert r.status_code == 404


def test_run_schedule_logs_final_outcome(client, tmp_path, monkeypatch):
    import json

    log = tmp_path / "cron.log"
    monkeypatch.setattr(schedules, "CRON_LOG", str(log))
    sid = client.post("/schedules", json={"cron": "0 9 * * *", "prompt": "hi"}).json()["id"]

    with patch.object(server, "_stream_exec", return_value=("ok", 0, False)):
        run_id = client.post(f"/schedules/{sid}/run").json()["id"]
        server._get(run_id).done.wait(timeout=5)
        # _record runs off-thread after done — give it a beat to write the log.
        for _ in range(50):
            if log.exists() and log.read_text().strip():
                break
            time.sleep(0.05)

    entry = json.loads(log.read_text().splitlines()[-1])
    assert entry["event"] == "outcome"
    assert entry["schedule_id"] == sid
    assert entry["run_id"] == run_id
    assert entry["status"] == "completed"


def test_create_schedule_with_channel(client):
    r = client.post(
        "/schedules",
        json={"cron": "*/5 * * * *", "prompt": "joke", "channel": "C0123ABC"},
    )
    assert r.status_code == 201
    assert r.json()["channel"] == "C0123ABC"


def test_run_schedule_delivers_to_slack(client):
    sid = client.post(
        "/schedules",
        json={"cron": "*/5 * * * *", "prompt": "joke", "channel": "C0123ABC"},
    ).json()["id"]

    posted = {}

    def fake_post(channel, text, **kwargs):
        posted["channel"] = channel
        posted["text"] = text
        return True, ""

    # Simulate Bob streaming its answer into the run's output buffer (the real
    # _stream_exec appends to the sink; a bare return value would not).
    def fake_exec(sink, cmd, cwd, timeout):
        out = "---output---\nknock knock\n---output---\n"
        sink._append(out)
        return out, 0, False

    import slack_bot

    with patch.object(server, "_stream_exec", side_effect=fake_exec), \
         patch.object(slack_bot, "post_message", side_effect=fake_post):
        r = client.post(f"/schedules/{sid}/run")
        assert r.status_code == 202
        run_id = r.json()["id"]
        server._get(run_id).done.wait(timeout=5)
        # The delivery runs in a daemon thread after done — give it a beat.
        for _ in range(50):
            if posted:
                break
            time.sleep(0.05)

    assert posted.get("channel") == "C0123ABC"
    assert "knock knock" in posted.get("text", "")
