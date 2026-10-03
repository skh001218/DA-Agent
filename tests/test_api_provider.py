import json

import httpx
import pytest

from da_agent.api_provider import GeminiProvider, configured_provider


def provider(tmp_path, handler):
    key = tmp_path / "key"
    key.write_text("sk-test-secret", encoding="utf-8")
    return GeminiProvider(key_file=key, model="test-model", http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def completed(text="검토 완료"):
    return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": text}]}}]}


def test_explicit_provider_selection_and_missing_key(tmp_path, monkeypatch):
    monkeypatch.setenv("DA_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY_FILE", str(tmp_path / "missing"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-unrelated-no-fallback")
    value = configured_provider()
    assert not value.status()["configured"]
    assert value.review([])["reason"] == "api_key_missing"
    monkeypatch.setenv("DA_LLM_PROVIDER", "typo")
    with pytest.raises(ValueError):
        configured_provider()


def test_completed_request_and_secret_boundary(tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=completed())
    value = provider(tmp_path, handler)
    assert not value.status()["inference_verified"]
    messages = [{"role": "developer", "content": "review"}, {"role": "user", "content": "evidence"}]
    result = value.review(messages)
    assert result == {"state": "completed", "text": "검토 완료", "model": "test-model"}
    request = requests[0]
    assert request.url == "https://generativelanguage.googleapis.com/v1beta/models/test-model:generateContent"
    assert request.headers["x-goog-api-key"] == "sk-test-secret"
    assert json.loads(request.content) == {"systemInstruction": {"parts": [{"text": "review"}]}, "contents": [{"role": "user", "parts": [{"text": "evidence"}]}], "store": False, "generationConfig": {"maxOutputTokens": 4096}}
    assert "key=" not in str(request.url)
    assert value.status()["inference_verified"]
    assert "sk-test-secret" not in json.dumps(value.status())


@pytest.mark.parametrize("status,code,expected", [(401,"invalid_api_key","api_key_invalid"), (403,None,"api_permission_denied"), (404,None,"model_unavailable"), (429,"RESOURCE_EXHAUSTED","api_rate_limited"), (429,"rate_limit_exceeded","api_rate_limited"), (500,None,"api_unavailable"), (400,None,"api_request_invalid"), (400,"API_KEY_INVALID","api_key_invalid")])
def test_sanitized_errors_without_retry(tmp_path, status, code, expected):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={"error": {"details": [{"reason": code}], "message": "sk-test-secret user material"}})
    value = provider(tmp_path, handler)
    result = value.review([])
    assert result["reason"] == expected
    assert len(requests) == 1
    assert "sk-test-secret" not in json.dumps(result)
    assert not value.status()["inference_verified"]


@pytest.mark.parametrize("body", [{"candidates":[{"finishReason":"MAX_TOKENS", "content":{"parts":[{"text":"partial"}]}}]}, completed(""), {"candidates": [None]}, {"promptFeedback":{"blockReason":"SAFETY"}}, {"candidates":[{"finishReason":"SAFETY"}]}])
def test_partial_empty_malformed_are_not_success(tmp_path, body):
    value = provider(tmp_path, lambda request: httpx.Response(200, json=body))
    result = value.review([])
    assert result["state"] == "error" and "text" not in result


def test_timeout_and_model_listing(tmp_path):
    def timeout(request):
        raise httpx.ReadTimeout("sk-test-secret", request=request)
    assert provider(tmp_path, timeout).review([])["reason"] == "api_timeout"
    value = provider(tmp_path, lambda request: httpx.Response(200, json={"displayName":"test-model", "supportedGenerationMethods":["generateContent"]}))
    assert value.models()["models"] == [{"slug":"test-model", "display_name":"test-model"}]
    assert not value.status()["inference_verified"]


def test_key_rotation_and_auth_failure_clear_verification(tmp_path):
    value = provider(tmp_path, lambda request: httpx.Response(200, json=completed()))
    value.review([])
    assert value.status()["inference_verified"]
    value.key_file.write_text("changed-test-key", encoding="utf-8")
    assert not value.status()["inference_verified"]
    value.review([])
    assert value.status()["inference_verified"]
    value.http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(403, json={})))
    value.review([])
    assert not value.status()["inference_verified"]
