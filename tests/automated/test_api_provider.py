import json

import httpx
import pytest

from da_agent.api_provider import GeminiProvider, configured_provider


@pytest.fixture(autouse=True)
def isolate_gemini_environment(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def test_environment_key_overrides_file_without_exposure(tmp_path, monkeypatch):
    requests = []
    monkeypatch.setenv("GEMINI_API_KEY", "  env-test-secret  ")
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=completed())
    value = provider(tmp_path, handler)
    assert value.review([{'role': 'user', 'content': 'test'}])["state"] == "completed"
    assert requests[0].headers["x-goog-api-key"] == "env-test-secret"
    assert "env-test-secret" not in json.dumps(value.status())
    assert value.status()["inference_verified"]
    monkeypatch.setenv("GEMINI_API_KEY", "another-env-secret")
    assert not value.status()["inference_verified"]


@pytest.mark.parametrize("environment_key", ["", "   "])
def test_empty_environment_key_uses_file(tmp_path, monkeypatch, environment_key):
    monkeypatch.setenv("GEMINI_API_KEY", environment_key)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=completed())
    value = provider(tmp_path, handler)
    assert value.review([])["state"] == "completed"
    assert requests[0].headers["x-goog-api-key"] == "sk-test-secret"


def test_invalid_environment_key_does_not_retry_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "invalid-env-secret")
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(401, json={"error": {"message": "invalid-env-secret"}})
    value = provider(tmp_path, handler)
    result = value.review([])
    assert result["reason"] == "api_key_invalid"
    assert len(requests) == 1
    assert requests[0].headers["x-goog-api-key"] == "invalid-env-secret"
    assert "invalid-env-secret" not in json.dumps(result)


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


@pytest.mark.parametrize('kind', ['http', 'timeout', 'transport', 'invalid_json'])
def test_failure_diagnostics_are_safe_and_survive_normalization(tmp_path, kind):
    from da_agent.reviews import normalize_ai
    def handler(request):
        if kind=='http': return httpx.Response(503,json={'error':{'message':'sk-test-secret'}})
        if kind=='timeout': raise httpx.ReadTimeout('sk-test-secret',request=request)
        if kind=='transport': raise httpx.ConnectError('sk-test-secret',request=request)
        return httpx.Response(200,text='sk-test-secret')
    result=provider(tmp_path,handler).review([{'role':'user','content':'private submission'}])
    diagnostic=normalize_ai(result)['error']['provider_diagnostic']
    assert diagnostic['timeout']==(kind=='timeout')
    assert len(diagnostic['request_id'])==36
    assert 'sk-test-secret' not in json.dumps(result)
    assert 'private submission' not in json.dumps(result)


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


def test_provider_returns_observed_usage_without_estimation(tmp_path):
    payload = dict(completed(), usageMetadata={'promptTokenCount': 11, 'candidatesTokenCount': 7, 'totalTokenCount': 18})
    value = provider(tmp_path, lambda request: httpx.Response(200, json=payload))
    assert value.review([])['usage'] == {'input_tokens': 11, 'output_tokens': 7, 'total_tokens': 18}


def test_coaching_provider_enforces_five_fields_and_actual_source_ids(tmp_path):
    from da_agent.coaching import coaching_messages, coaching_schema
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=completed('{}'))
    context = {'contract_version': 'coaching-v2', 'sources': [{'id': 'public-task', 'state': 'public'}], 'message': '질문'}
    provider(tmp_path, handler).review(coaching_messages(context))
    assert requests[0]['generationConfig']['responseMimeType'] == 'application/json'
    schema = requests[0]['generationConfig']['responseJsonSchema']
    assert set(schema['required']) == {'action_type', 'reason', 'next_action', 'evidence_ids', 'uncertainty'}
    assert schema['additionalProperties'] is False
    assert schema['properties']['evidence_ids']['items']['enum'] == ['public-task']
    assert 'evidence_state' not in schema['properties']
    assert coaching_schema({'sources': []})['properties']['evidence_ids']['maxItems'] == 0


def test_adaptive_json_mode_preserves_general_text_calls(tmp_path):
    requests=[]
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json=completed('{}'))
    value=provider(tmp_path,handler)
    for envelope in ({'request':{},'schema':{'title':'Recipe'}},{'request':{},'task':{},'dictionary':{},'private_generation_recipe':{}}):
        value.review([{'role':'user','content':json.dumps(envelope)}])
        assert requests[-1]['generationConfig']=={'maxOutputTokens':8192,'responseMimeType':'application/json'}
    value.review([{'role':'user','content':'코칭 질문'}])
    assert requests[-1]['generationConfig']=={'maxOutputTokens':4096}


def test_interpretation_json_schema_and_capability_bounds(tmp_path):
    from da_agent.task_planner import interpretation_messages
    from da_agent.task_contracts import RequestV2
    from da_agent.capabilities import CAPABILITIES
    requests=[]
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json=completed('{}'))
    value=provider(tmp_path,handler)
    data=RequestV2(contract_version='request-v2',request_id='test',message='접속 분석',data_mode='existing')
    caps=[dict(c,domain='access',tables=['users','sessions'],supported_topic='return_observation',scope='D1~D7 미재접속') for c in CAPABILITIES]
    value.review(interpretation_messages(data,caps,[]))
    config=requests[-1]['generationConfig']
    assert config['responseMimeType']=='application/json'
    schema=config['responseJsonSchema']
    assert schema['additionalProperties'] is False
    assert set(schema['required'])=={'analysis_topic','capability_id','difficulty','task_kind','goal','questions','unsupported','reason'}
    assert schema['properties']['capability_id']['anyOf'][0]['enum']==[c['capability_id'] for c in CAPABILITIES]
    assert config['maxOutputTokens']==4096


def test_v3_condition_review_schema_without_model_grades(tmp_path):
    from da_agent.evaluation import freeze_evaluation
    from da_agent.evaluation_v3 import review_messages, review_envelope
    calls=[]
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json=completed('{}'))
    p={'evaluation_version':'request-review-v3','task_kind':'design','weights':{'analysis_approach':100},'completion_conditions':['분석 방법 설계']}
    f=freeze_evaluation(p,{'weights':p['weights']})
    provider(tmp_path,handler).review(review_messages(review_envelope(p,{'content':{'hypothesis':'방법 설계'},'claims':[]},[],f)))
    config=calls[-1]['generationConfig']
    assert config['maxOutputTokens']==8192
    assert config['responseMimeType']=='application/json'
    schema=config['responseJsonSchema']['properties']['criteria']['items']
    assert set(schema['properties'])=={'key','conditions'}
    assert schema['additionalProperties'] is False


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
