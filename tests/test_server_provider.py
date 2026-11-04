import io
import json
import urllib.error

import pytest

from chimera.server_provider import LlamaServerManager, ServerRequestError


class FakeResponse:
    def __init__(self, chunks):
        self.chunks = chunks
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def __iter__(self):
        return iter(self.chunks)


def make_manager():
    manager = LlamaServerManager(
        executable=None,
        model=None,
        timeout=2,
        port=12345,
    )
    # These tests exercise transport construction and parsing without starting
    # a real model process or making a network request.
    manager.start = lambda: None
    return manager


def test_completion_sends_json_schema_and_parses_sse(monkeypatch):
    manager = make_manager()
    schema = {
        "oneOf": [
            {
                "type": "object",
                "properties": {"type": {"const": "message"}, "content": {"type": "string"}},
                "required": ["type", "content"],
                "additionalProperties": False,
            }
        ]
    }
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.header_items())
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse([
            b'data: {"content":"{\\\"type\\\":", "stop":false}\n',
            b'data: {"content":"\\\"message\\\",\\\"content\\\":\\\"hi\\\"}", "stop":false}\n',
            b'data: [DONE]\n',
        ])

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    output, metrics = manager.complete(
        "safe prompt",
        96,
        json_schema=schema,
    )

    assert captured["url"] == "http://127.0.0.1:12345/completion"
    assert captured["timeout"] == 2
    assert captured["payload"]["json_schema"] == schema
    assert captured["payload"]["stream"] is True
    assert captured["payload"]["n_predict"] == 96
    assert "grammar" not in captured["payload"]
    assert "safe prompt" not in json.dumps(metrics)
    assert output == '{"type":"message","content":"hi"}'
    assert metrics["json_schema"] is True


def test_completion_rejects_ambiguous_structured_output_configuration():
    manager = make_manager()

    with pytest.raises(ValueError, match="either grammar or json_schema"):
        manager.complete(
            "prompt",
            32,
            grammar='root ::= "ok"',
            json_schema={"type": "object"},
        )


def test_http_400_preserves_bounded_server_error_diagnostics(monkeypatch):
    manager = make_manager()
    prompt = "THIS PROMPT MUST NOT APPEAR IN DIAGNOSTICS"
    body = json.dumps({
        "error": {
            "code": 400,
            "message": "Failed to parse JSON schema",
            "type": "invalid_request_error",
        }
    }).encode("utf-8")

    def reject_request(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(body),
        )

    monkeypatch.setattr("urllib.request.urlopen", reject_request)

    with pytest.raises(ServerRequestError) as captured:
        manager.complete(prompt, 64, json_schema={"oneOf": []})

    error = captured.value
    diagnostic = error.as_dict()
    assert error.status_code == 400
    assert error.endpoint == "http://127.0.0.1:12345/completion"
    assert "Failed to parse JSON schema" in str(error)
    assert "invalid_request_error" in str(error)
    assert diagnostic["request_config"]["structured_output"] == "json_schema"
    assert diagnostic["request_config"]["prompt_chars"] == len(prompt)
    assert prompt not in str(error)
    assert prompt not in json.dumps(diagnostic)
    assert manager.last_request_diagnostic["status_code"] == 400


def test_http_error_body_is_bounded(monkeypatch):
    manager = make_manager()
    body = ("x" * 7000).encode()

    def reject_request(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 400, "Bad Request", hdrs=None, fp=io.BytesIO(body)
        )

    monkeypatch.setattr("urllib.request.urlopen", reject_request)
    with pytest.raises(ServerRequestError) as captured:
        manager.complete("prompt", 16, json_schema={"type": "object"})

    assert len(captured.value.response_body) <= 4096
    assert captured.value.body_truncated is True
    assert "response body truncated" in str(captured.value)


def test_local_server_timeout_is_reported_without_retry_or_fallback(monkeypatch):
    manager = make_manager()
    calls = []

    def timeout_request(request, timeout):
        calls.append(request.full_url)
        raise TimeoutError("fixture timeout")

    monkeypatch.setattr("urllib.request.urlopen", timeout_request)

    with pytest.raises(TimeoutError, match="timed out after 2s"):
        manager.complete("prompt", 16, json_schema={"type": "object"})

    assert calls == ["http://127.0.0.1:12345/completion"]
    assert manager.state == "failed"


@pytest.mark.parametrize(
    ("status_code", "message"),
    [
        (400, "invalid request payload"),
        (500, "internal server failure"),
    ],
)
def test_http_4xx_and_5xx_are_reported_with_server_body(monkeypatch, status_code, message):
    manager = make_manager()
    body = json.dumps({
        "error": {"code": status_code, "message": message, "type": "server_error"}
    }).encode("utf-8")

    def reject_request(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            status_code,
            "Request failed",
            hdrs=None,
            fp=io.BytesIO(body),
        )

    monkeypatch.setattr("urllib.request.urlopen", reject_request)
    with pytest.raises(ServerRequestError) as captured:
        manager.complete("prompt", 16, json_schema={"type": "object"})

    assert captured.value.status_code == status_code
    assert message in str(captured.value)


def test_chat_completion_uses_role_messages_and_llama_cpp_json_schema(monkeypatch):
    manager = make_manager()
    messages = [
        {"role": "system", "content": "Return one JSON decision."},
        {"role": "user", "content": "Open Paint."},
        {"role": "user", "content": "RUNTIME TOOL RESULT: executable lookup failed."},
    ]
    schema = {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["message", "tool_call"]},
            "content": {"type": "string"},
        },
        "required": ["type"],
    }
    captured = {}
    expected = '{"type":"message","content":"I will inspect installed applications."}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse([
            ("data: " + json.dumps({
                "choices": [{"delta": {"content": expected}}]
            }) + "\n").encode(),
            b"data: [DONE]\n",
        ])

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    output, metrics = manager.complete(
        "compatibility prompt",
        512,
        json_schema=schema,
        messages=messages,
    )

    assert captured["url"] == "http://127.0.0.1:12345/v1/chat/completions"
    assert captured["payload"]["messages"] == messages
    assert captured["payload"]["response_format"] == {
        "type": "json_schema",
        "schema": schema,
    }
    assert captured["payload"]["max_tokens"] == 512
    assert captured["payload"]["stream"] is True
    assert output == expected
    assert metrics["chat_template"] is True
    assert metrics["json_schema"] is True
    assert "compatibility prompt" not in json.dumps(captured["payload"])


def test_chat_completion_rejects_unsupported_message_roles():
    manager = make_manager()

    with pytest.raises(ValueError, match="supported roles"):
        manager.complete(
            "",
            32,
            messages=[{"role": "tool", "content": "not a supported chat role"}],
        )


def test_chat_completion_parses_split_streamed_content_and_reasoning(monkeypatch):
    manager = make_manager()
    pieces = []

    def fake_urlopen(_request, _timeout):
        return FakeResponse([
            b'data: {"choices":[{"delta":{"reasoning_content":"inspect "}}]}\n',
            b'data: {"choices":[{"delta":{"content":"{\"type\":"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"\"message\"}"}}]}\n',
            b"data: [DONE]\n",
        ])

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    output, metrics = manager.complete(
        "",
        32,
        messages=[{"role": "user", "content": "Return JSON."}],
        on_delta=pieces.append,
    )

    assert output == '{"type":"message"}'
    assert "".join(pieces) == output
    assert metrics["reasoning_tokens_estimate"] == 1
    assert metrics["chat_template"] is True


def test_server_manager_defaults_to_adaptive_gpu_offload():
    manager = make_manager()

    assert manager.gpu_layers == "auto"
