"""Pure formatting, parsing, credential and local CLI checks; no API mocks."""

import json
import os
import stat

from click.testing import CliRunner
import httpx
import pytest

import gmi.api as api
from gmi.api import check_response, client, sse_events
from gmi.cli import main, model_table, price_per_million, wrap_stream
from gmi.config import GMIError, config_path, mask_key, read_key, resolve_key, resolve_model, save_config, save_key


@pytest.fixture(autouse=True)
def private_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("GMI_API_KEY", raising=False)


def test_config_permissions_and_round_trip():
    save_key('test-key-"quoted"-abcd')
    assert read_key() == 'test-key-"quoted"-abcd'
    if os.name != "nt":  # Windows has no POSIX permission bits.
        assert stat.S_IMODE(config_path().stat().st_mode) == 0o600
    save_key("replacement")
    assert read_key() == "replacement"
    if os.name != "nt":
        assert stat.S_IMODE(config_path().stat().st_mode) == 0o600
    assert list(config_path().parent.iterdir()) == [config_path()]


def test_precedence(monkeypatch):
    save_key("config-key")
    assert resolve_key() == "config-key"
    monkeypatch.setenv("GMI_API_KEY", "env-key")
    assert resolve_key() == "env-key"
    assert resolve_key("flag-key") == "flag-key"


def test_broken_config():
    config_path().parent.mkdir(parents=True)
    config_path().write_text("api_key = [broken")
    with pytest.raises(GMIError, match="Cannot read config"):
        read_key()


@pytest.mark.parametrize("key", ["x", "abcd", "12345678"])
def test_short_keys_are_not_exposed(key):
    assert key not in mask_key(key)


def test_mask_key():
    assert mask_key("test-secret-abcdefgh") == "gmi_...efgh"


@pytest.mark.parametrize("value,expected", [("0.0000002", "0.20"), ("0.00000125", "1.25"), ("0", "0.00"), (None, "-")])
def test_prices(value, expected):
    assert price_per_million(value) == expected


def test_table():
    table = model_table([{"id": "test/model", "context_length": 4096,
                          "pricing": {"prompt": "0.0000002", "completion": "0.00000125"}}])
    lines = table.splitlines()
    assert lines[0] == "  ID          CONTEXT  PROMPT $/1M  COMPLETION $/1M"
    assert lines[1] == "  test/model  4,096    0.20         1.25"
    id_offset = lines[0].index("ID")
    assert all(line.index("test/model") == id_offset for line in lines[1:])
    marked = model_table([{"id": "test/model", "context_length": 1050000,
                           "pricing": {"prompt": "0.000002", "completion": "0.00001"}}], "test/model")
    assert marked.splitlines()[0].startswith("  ID")
    assert marked.splitlines()[1].startswith("* test/model")
    assert "1,050,000" in marked
    assert "2.00" in marked and "10.00" in marked
    assert len(model_table([]).splitlines()) == 1


def test_stream_wrapper_handles_chunks_newlines_and_long_words():
    chunks = ["hel", "lo wor", "ld\nnext ", "https://example.com/verylong"]
    output = "".join(wrap_stream(chunks, 10))
    assert output == "hello \nworld\nnext https\n://example\n.com/veryl\nong"
    assert "hello" in output and "world" in output
    assert "\n" in output
    assert max(map(len, output.splitlines())) <= 10


def test_sse_parser():
    lines = [": keepalive", "event: message", 'data: {"choices":', 'data: []}', "", "data: [DONE]", ""]
    events = list(sse_events(lines))
    assert json.loads(events[0]) == {"choices": []}
    assert events[1] == "[DONE]"
    assert list(sse_events(["data:tail"])) == ["tail"]


@pytest.mark.parametrize("status,match", [(401, "API key rejected"),
                                          (404, "Model test/model was not found on GMI"),
                                          (429, "wait and retry"), (503, "HTTP 503")])
def test_status_error_formatting(status, match):
    # Exercise only the status formatter, with no HTTP request or mock transport.
    with pytest.raises(GMIError, match=match):
        check_response(httpx.Response(status), "test/model")


def test_429_classification():
    overloaded = httpx.Response(429, json={"error": {"type": "service_unavailable", "message": "all endpoints overloaded"}})
    limited = httpx.Response(429, json={"error": {"type": "rate_limit", "message": "too many requests"}})
    with pytest.raises(GMIError, match="overloaded"):
        check_response(overloaded)
    with pytest.raises(GMIError, match="Rate limit reached"):
        check_response(limited)


def test_429_retry_logic(monkeypatch):
    responses = [httpx.Response(429, headers={"retry-after": "60"},
                                json={"error": {"type": "service_unavailable", "message": "overloaded"}}),
                 httpx.Response(200, json={"choices": []})]
    class FakeConnection:
        def post(self, *args, **kwargs):
            return responses.pop(0)
    class FakeClient:
        def __enter__(self): return FakeConnection()
        def __exit__(self, *args): pass
    monkeypatch.setattr(api, "client", lambda key: FakeClient())
    sleeps = []
    monkeypatch.setattr(api.time, "sleep", sleeps.append)
    assert api.complete("key", {"model": "test/model"}) == {"choices": []}
    assert sleeps == [30.0]


def test_network_error_formatting():
    with pytest.raises(GMIError, match="check your connection"):
        with client("unit-only-key"):
            raise httpx.ConnectError("secret diagnostic must not be displayed")


@pytest.mark.parametrize("args", [["models"], ["chat", "hello"], ["auth", "status"]])
def test_missing_key(args):
    result = CliRunner(mix_stderr=False).invoke(main, args)
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1
    assert "Missing API key" in result.stderr


def test_json_login_never_prompts():
    result = CliRunner(mix_stderr=False).invoke(main, ["--json", "auth", "login"])
    assert result.exit_code == 1
    assert "Provide --stdin" in result.stderr
    assert result.stdout == ""


def test_empty_stdin_login():
    result = CliRunner(mix_stderr=False).invoke(main, ["auth", "login", "--stdin"], input="\n")
    assert result.exit_code == 1
    assert "non-empty API key" in result.stderr
    assert not config_path().exists()


def test_logout_confirmation_and_json(monkeypatch):
    save_key("unit-only-key")
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(main, ["auth", "logout", "--json"])
    assert result.exit_code == 1
    assert config_path().exists()
    result = runner.invoke(main, ["auth", "logout"], input="n\n")
    assert result.exit_code != 0
    assert config_path().exists()
    monkeypatch.setenv("GMI_API_KEY", "environment-key")
    result = runner.invoke(main, ["auth", "logout", "--yes", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"logged_out": True}
    assert not config_path().exists()
    assert resolve_key() == "environment-key"


def test_empty_prompt_and_invalid_token_limit(monkeypatch):
    monkeypatch.setenv("GMI_API_KEY", "unit-only-key")
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(main, ["chat"])
    assert result.exit_code == 1
    assert "Provide a prompt" in result.stderr
    result = runner.invoke(main, ["chat", "hello", "--max-tokens", "0"])
    assert result.exit_code == 2


def test_default_model_and_help():
    result = CliRunner().invoke(main, ["chat", "--help"])
    assert result.exit_code == 0
    assert "openai/gpt-6.1-sol" in result.stdout


def test_default_model_precedence(monkeypatch):
    save_config(default_model="test/model-a")
    assert resolve_model() == "test/model-a"
    monkeypatch.setenv("GMI_MODEL", "test/model-b")
    assert resolve_model() == "test/model-b"
    assert resolve_model("test/model-a") == "test/model-a"
