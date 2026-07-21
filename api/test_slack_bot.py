"""Unit tests for the Slack bot's pure logic.

These never open a Slack connection or hit the network: `urllib.request.urlopen`
is mocked, so they run fast and offline (same discipline as test_server.py).
"""
import io
import json
import urllib.error
from unittest.mock import patch

import slack_bot


# --------------------------------------------------------------------------- #
# should_handle
# --------------------------------------------------------------------------- #
def test_should_handle_accepts_plain_user_message():
    assert slack_bot.should_handle({"text": "hola bob", "channel": "C1", "user": "U1"})


def test_should_handle_rejects_bot_messages():
    # This is the anti-loop guard: the bot's own posts carry a bot_id.
    assert not slack_bot.should_handle({"text": "hi", "bot_id": "B1"})


def test_should_handle_rejects_subtypes():
    assert not slack_bot.should_handle({"text": "x", "subtype": "message_changed"})


def test_should_handle_rejects_empty_text():
    assert not slack_bot.should_handle({"text": "   ", "channel": "C1"})
    assert not slack_bot.should_handle({"channel": "C1"})


def test_should_handle_respects_allowlist():
    ev = {"text": "hi", "channel": "C_OTHER"}
    assert not slack_bot.should_handle(ev, allowed_channels={"C_OK"})
    assert slack_bot.should_handle({"text": "hi", "channel": "C_OK"}, allowed_channels={"C_OK"})


def test_parse_allowed():
    assert slack_bot._parse_allowed(None) is None
    assert slack_bot._parse_allowed("") is None
    assert slack_bot._parse_allowed(" C1 , C2 ,") == {"C1", "C2"}


# --------------------------------------------------------------------------- #
# run_prompt
# --------------------------------------------------------------------------- #
class _FakeResp:
    def __init__(self, body: dict):
        self._data = json.dumps(body).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_run_prompt_returns_output_on_success():
    resp = _FakeResp({"ok": True, "exit_code": 0, "output": "done!"})
    with patch.object(slack_bot.urllib.request, "urlopen", return_value=resp) as urlopen:
        out = slack_bot.run_prompt("do it", harness_url="http://h:8080", mode="reviewer")

    assert out == "done!"
    # POSTs JSON to /invoke with the prompt and forwarded mode.
    req = urlopen.call_args.args[0]
    assert req.full_url == "http://h:8080/invoke"
    assert json.loads(req.data.decode()) == {"prompt": "do it", "mode": "reviewer"}


def test_run_prompt_forwards_workdir():
    resp = _FakeResp({"ok": True, "exit_code": 0, "output": "ok"})
    with patch.object(slack_bot.urllib.request, "urlopen", return_value=resp) as urlopen:
        slack_bot.run_prompt("x", harness_url="http://h:8080", workdir="/")
    assert json.loads(urlopen.call_args.args[0].data.decode())["workdir"] == "/"


def test_run_prompt_reports_bob_failure():
    resp = _FakeResp({"ok": False, "exit_code": 2, "output": "", "error": "boom"})
    with patch.object(slack_bot.urllib.request, "urlopen", return_value=resp):
        out = slack_bot.run_prompt("x", harness_url="http://h:8080")
    assert "code 2" in out and "boom" in out


def test_run_prompt_handles_http_error():
    err = urllib.error.HTTPError(
        url="http://h:8080/invoke", code=500, msg="err", hdrs=None,
        fp=io.BytesIO(b"kaboom"),
    )
    with patch.object(slack_bot.urllib.request, "urlopen", side_effect=err):
        out = slack_bot.run_prompt("x", harness_url="http://h:8080")
    assert "HTTP 500" in out and "kaboom" in out


def test_run_prompt_handles_unreachable_harness():
    err = urllib.error.URLError("connection refused")
    with patch.object(slack_bot.urllib.request, "urlopen", side_effect=err):
        out = slack_bot.run_prompt("x", harness_url="http://h:8080")
    assert "Could not reach the harness" in out


# --------------------------------------------------------------------------- #
# clean_output
# --------------------------------------------------------------------------- #
def test_clean_output_extracts_answer_between_markers():
    raw = (
        "<thinking>\nThe user is greeting me...\n</thinking>\n\n"
        "Sí, te escucho...[using tool attempt_completion: done | Cost: 0.08]\n"
        "---output---\n\n"
        "Sí, te escucho perfectamente. Soy Bob Shell.\n\n"
        "---output---\n"
    )
    assert slack_bot.clean_output(raw) == "Sí, te escucho perfectamente. Soy Bob Shell."


def test_clean_output_picks_last_block_not_tool_summary():
    # Bob emits one ---output--- block per tool; the answer is the LAST one,
    # not the first ("Listed 20 item(s).").
    raw = (
        "<thinking>plan</thinking>\n[using tool list_files: .]\n"
        "---output---\nListed 20 item(s).\n---output---\n"
        "<thinking>done</thinking>\n[using tool attempt_completion: ok]\n"
        "---output---\n\n[DIR] app\n[DIR] bin\n[DIR] var\n\n---output---\n"
    )
    assert slack_bot.clean_output(raw) == "[DIR] app\n[DIR] bin\n[DIR] var"


def test_clean_output_strips_thinking_and_tool_noise_without_markers():
    raw = "<thinking>plan it</thinking>\nHecho.[using tool read_file: ok]"
    assert slack_bot.clean_output(raw) == "Hecho."


def test_clean_output_empty():
    assert slack_bot.clean_output("") == ""


def test_run_prompt_cleans_success_output():
    raw = "<thinking>x</thinking>\n---output---\nHola\n---output---"
    resp = _FakeResp({"ok": True, "exit_code": 0, "output": raw})
    with patch.object(slack_bot.urllib.request, "urlopen", return_value=resp):
        assert slack_bot.run_prompt("hi", harness_url="http://h:8080") == "Hola"


# --------------------------------------------------------------------------- #
# build_reply
# --------------------------------------------------------------------------- #
def test_build_reply_plain_prose_stays_plain():
    assert slack_bot.build_reply("Listo, creé el archivo en /workspace.") == \
        "Listo, creé el archivo en /workspace."


def test_build_reply_wraps_diff_in_code_block():
    diff = (
        "Index: hello.py\n"
        "--- hello.py\tOriginal\n"
        "+++ hello.py\tWritten\n"
        "@@ -0,0 +1,3 @@\n"
        '+print("Hello, World!")'
    )
    reply = slack_bot.build_reply(diff)
    assert reply.startswith("```\n") and reply.endswith("\n```")
    assert "Index: hello.py" in reply


def test_build_reply_wraps_source_code():
    code = 'def add(a, b):\n    return a + b\n\nprint(add(1, 2))'
    reply = slack_bot.build_reply(code)
    assert reply.startswith("```")


def test_build_reply_passes_through_existing_fences():
    fenced = "Aquí tienes:\n```python\nprint(1)\n```"
    assert slack_bot.build_reply(fenced) == fenced


def test_looks_like_code_detects_and_rejects():
    assert slack_bot.looks_like_code("@@ -1 +1 @@\n+x = 1")
    assert slack_bot.looks_like_code("#!/usr/bin/env python3\nprint(1)")
    assert not slack_bot.looks_like_code("Hola, ¿en qué te ayudo hoy?")


def test_build_reply_truncates_long_output():
    reply = slack_bot.build_reply("a" * (slack_bot.MAX_REPLY_CHARS + 500))
    assert "output truncated" in reply
    assert len(reply) < slack_bot.MAX_REPLY_CHARS + 100


# --------------------------------------------------------------------------- #
# Thread context: format_thread / build_conversation_prompt
# --------------------------------------------------------------------------- #
def test_format_thread_labels_roles_and_skips_noise():
    msgs = [
        {"ts": "1", "text": "crea un hello world", "user": "U1"},
        {"ts": "2", "text": "hecho", "bot_id": "B1"},
        {"ts": "3", "text": slack_bot.THINKING_TEXT, "bot_id": "B1"},  # placeholder
        {"ts": "4", "subtype": "channel_join", "text": "joined"},       # noise
        {"ts": "5", "text": "donde quedó?", "user": "U1"},              # current msg
    ]
    out = slack_bot.format_thread(msgs, current_ts="5")
    assert out == "User: crea un hello world\nAssistant: hecho"


def test_format_thread_caps_message_count():
    msgs = [{"ts": str(i), "text": f"m{i}", "user": "U1"} for i in range(40)]
    out = slack_bot.format_thread(msgs)
    assert len(out.splitlines()) == slack_bot.MAX_HISTORY_MESSAGES


def test_build_conversation_prompt_without_history_has_guidance_and_message():
    prompt = slack_bot.build_conversation_prompt("", "hola")
    assert "Answer guidance" in prompt
    assert prompt.strip().endswith("hola")


def test_build_conversation_prompt_with_history_wraps_context():
    prompt = slack_bot.build_conversation_prompt("User: hi\nAssistant: hello", "y ahora?")
    assert "Answer guidance" in prompt
    assert "conversation so far" in prompt
    assert "User: hi" in prompt and "Assistant: hello" in prompt
    assert prompt.strip().endswith("User: y ahora?")
