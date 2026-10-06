import json

import httpx
import pytest

from da_agent.discord_provider import DiscordGemmaProvider


def test_quota_diagnostics_keep_limit_identity_without_upstream_secrets(tmp_path):
    path=tmp_path/'key'; path.write_text('test-secret')
    body={'error':{'message':'private upstream text','details':[
        {'retryDelay':'12s'},
        {'violations':[{'quotaMetric':'generate_content_free_tier_requests',
            'quotaId':'RequestsPerMinutePerModel','quotaValue':'30',
            'quotaDimensions':{'project':'private-project'}}]}]}}
    client=httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(429,json=body)))
    result=DiscordGemmaProvider(key_file=path,http_client=client).review([{'role':'user','content':'x'}])
    assert result['reason']=='api_rate_limited'
    assert result['quota_diagnostic']==[{'retry_delay':'12s'},
        {'quotaMetric':'generate_content_free_tier_requests','quotaId':'RequestsPerMinutePerModel','quotaValue':'30'}]
    assert 'private' not in json.dumps(result) and 'test-secret' not in json.dumps(result)


@pytest.mark.parametrize('body', ['{"ok":true}', '```json\n{"ok":true}\n```', '```\n{"ok":true}\n```'])
def test_gemma_json_and_whole_fence_only(tmp_path, body):
    path = tmp_path / 'key'
    path.write_text('test-private-key')
    def respond(request):
        assert 'models/gemma-4-26b-a4b-it:generateContent' in str(request.url)
        assert request.headers['x-goog-api-key'] == 'test-private-key'
        assert json.loads(request.content)['generationConfig']['thinkingConfig']['thinkingLevel']=='minimal'
        return httpx.Response(200, json={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':body}]}}]})
    provider = DiscordGemmaProvider(key_file=path, http_client=httpx.Client(transport=httpx.MockTransport(respond)))
    result = provider.review([{'role':'user','content':'contract'}])
    assert result['state'] == 'completed' and json.loads(result['text']) == {'ok': True}


@pytest.mark.parametrize('body', ['prefix {"ok":true}', '[]', '```json\n{"ok":true}\n``` trailing', '{bad}'])
def test_invalid_json_fails_closed(tmp_path, body):
    path = tmp_path / 'key'
    path.write_text('test-key')
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':body}]}}]})))
    result = DiscordGemmaProvider(key_file=path, http_client=client).review([{'role':'user','content':'x'}])
    assert result['state'] == 'error' and result['reason'] == 'provider_invalid_json'


def test_private_json_diagnostic_preserves_original_and_exact_position(tmp_path):
    path = tmp_path / 'key'; path.write_text('secret-not-for-diagnostics')
    body = '```json\n{"count": 4,}\n```'
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200,
        json={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':body}]}}]})))
    provider = DiscordGemmaProvider(key_file=path, http_client=client, diagnostics_directory=tmp_path/'private')
    result = provider.review([{'role':'user','content':'private request not for diagnostics'}])
    diagnostic = result['json_diagnostic']
    assert diagnostic['kind'] == 'json_syntax' and diagnostic['fence_removed']
    assert diagnostic['line'] == 1 and diagnostic['column'] == 13 and diagnostic['position'] == 12
    record = json.loads((tmp_path/'private'/(diagnostic['diagnostic_id']+'.json')).read_text(encoding='utf-8'))
    assert record['raw_text'] == body and record['parsed_body'] == '{"count": 4,}'
    assert record['transport']['http_status'] == 200 and record['transport']['finish_reason'] == 'STOP'
    assert 'secret-not-for-diagnostics' not in json.dumps(record)
    assert 'private request not for diagnostics' not in json.dumps(record)
    assert result['text'] == '' and 'raw_text' not in result


@pytest.mark.parametrize('body', ['[]', '[{},{}]', '[4]'])
def test_valid_array_diagnostic_is_distinct_from_syntax_error(tmp_path, body):
    path = tmp_path/'key'; path.write_text('test-key')
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200,
        json={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':body}]}}]})))
    result = DiscordGemmaProvider(key_file=path,http_client=client).review([{'role':'user','content':'x'}])
    assert result['json_diagnostic']['kind'] == 'not_object'
    assert result['json_diagnostic']['parsed_type'] == 'list'
    assert 'diagnostic_id' not in result['json_diagnostic']


def test_single_object_array_normalizes_without_changing_contents(tmp_path):
    path = tmp_path/'key'; path.write_text('test-key')
    body = '[{"status":"ready","nested":{"count":501}}]'
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200,
        json={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':body}]}}]})))
    result = DiscordGemmaProvider(key_file=path,http_client=client).review([{'role':'user','content':'x'}])
    assert result['state'] == 'completed'
    assert result['normalized_envelope'] == 'single_object_array'
    assert json.loads(result['text']) == json.loads(body)[0]


def test_recipe_correction_keeps_request_and_feedback_in_single_user_envelope(tmp_path):
    from da_agent.adaptive_tasks import recipe_response_schema
    original = {'request':{'message':'활동량 비교'},'schema':recipe_response_schema()}
    path = tmp_path/'key'; path.write_text('test-key')
    def respond(req):
        body = json.loads(req.content)
        assert len(body['contents']) == 1 and body['contents'][0]['role'] == 'user'
        payload = json.loads(body['contents'][0]['parts'][0]['text'])
        assert payload['request'] == original['request'] and payload['schema'] == original['schema']
        assert payload['rejected_design_history'][0]['parts'][0]['text'] == '{"bad":true}'
        assert payload['rejected_design_history'][1]['parts'][0]['text'] == '첫 컬럼 ID를 수정하세요'
        assert 'responseJsonSchema' not in body['generationConfig']
        return httpx.Response(200,json={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'{"ok":true}'}]}}]})
    provider = DiscordGemmaProvider(key_file=path,http_client=httpx.Client(transport=httpx.MockTransport(respond)))
    messages = [{'role':'user','content':json.dumps(original)}, {'role':'assistant','content':'{"bad":true}'},
        {'role':'user','content':'첫 컬럼 ID를 수정하세요'}]
    assert provider.review(messages)['state'] == 'completed'


def test_json_diagnostic_write_failure_keeps_original_failure(tmp_path):
    path = tmp_path/'key'; path.write_text('test-key')
    blocked = tmp_path/'not-a-directory'; blocked.write_text('occupied')
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200,
        json={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'{bad}'}]}}]})))
    result = DiscordGemmaProvider(key_file=path,http_client=client,diagnostics_directory=blocked).review([{'role':'user','content':'x'}])
    assert result['reason'] == 'provider_invalid_json' and result['json_diagnostic']['diagnostic_write_failed']


def test_model_is_isolated_and_missing_key_does_not_call(tmp_path, monkeypatch):
    monkeypatch.setenv('GEMINI_MODEL', 'web-model')
    provider = DiscordGemmaProvider(key_file=tmp_path/'missing')
    assert provider.model == 'gemma-4-26b-a4b-it'
    assert provider.review([{'role':'user','content':'x'}])['reason'] == 'api_key_missing'
    with pytest.raises(ValueError):
        DiscordGemmaProvider(key_file=tmp_path/'missing', model='web-model')


def test_gemma_never_switches_to_configured_gemini_search(tmp_path, monkeypatch):
    monkeypatch.setenv('DISCORD_SEARCH_MODEL', 'gemini-3.5-flash-lite')
    monkeypatch.setenv('GEMINI_SEARCH_MODEL', 'gemini-3.5-flash-lite')
    calls = []
    path = tmp_path / 'key'
    path.write_text('test-key')
    def respond(request):
        calls.append(request)
        assert '/models/gemma-' in str(request.url)
        assert 'tools' not in json.loads(request.content)
        return httpx.Response(200, json={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'{"ok":true}'}]}}]})
    provider = DiscordGemmaProvider(key_file=path, http_client=httpx.Client(transport=httpx.MockTransport(respond)))
    assert provider.generation_mode == 'synthetic'
    assert provider.research([])['reason'] == 'research_unavailable'
    assert not calls
    assert provider.review([{'role':'user', 'content':'design'}])['state'] == 'completed'
    assert len(calls) == 1
