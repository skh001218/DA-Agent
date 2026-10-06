import json

import httpx
import pytest

from da_agent.discord_provider import DiscordGemmaProvider


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


def test_model_is_isolated_and_missing_key_does_not_call(tmp_path, monkeypatch):
    monkeypatch.setenv('GEMINI_MODEL', 'web-model')
    provider = DiscordGemmaProvider(key_file=tmp_path/'missing')
    assert provider.model == 'gemma-4-26b-a4b-it'
    assert provider.review([{'role':'user','content':'x'}])['reason'] == 'api_key_missing'
    with pytest.raises(ValueError):
        DiscordGemmaProvider(key_file=tmp_path/'missing', model='web-model')
