"""Explicit Gemma model for Discord; keep the running web provider untouched."""
import json
import re
import copy

import httpx

from .api_provider import GeminiProvider


class _GemmaClient:
    def __init__(self, client):
        self.client = client

    def post(self, url, **kwargs):
        body = copy.deepcopy(kwargs['json'])
        if '/models/gemma-4-' in url:
            body.setdefault('generationConfig', {})['thinkingConfig'] = {'thinkingLevel': 'minimal'}
        return self.client.post(url, **dict(kwargs, json=body))

    def get(self, *args, **kwargs):
        return self.client.get(*args, **kwargs)


class DiscordGemmaProvider(GeminiProvider):
    def __init__(self, *, key_file, model='gemma-4-26b-a4b-it', http_client=None):
        if not model.startswith('gemma-'):
            raise ValueError('Discord Gemma model must start with gemma-')
        super().__init__(key_file=key_file, model=model, http_client=_GemmaClient(http_client or httpx.Client(timeout=90,follow_redirects=False)))

    def review(self, messages, model=None):
        if model is not None and not model.startswith('gemma-'):
            return self._failed('model_unavailable')
        response = super().review(messages, model=model)
        if response.get('state') != 'completed':
            return response
        body = response['text'].strip()
        # Gemma can emit one Markdown JSON block even when JSON is requested.
        # Accept only a whole block: never fish JSON out of arbitrary prose.
        fenced = re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```', body, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            body = fenced.group(1).strip()
        try:
            parsed = json.loads(body)
            if not isinstance(parsed, dict):
                raise ValueError('Object required')
        except (ValueError, TypeError):
            return dict(response, state='error', reason='provider_invalid_json', text='')
        return dict(response, text=json.dumps(parsed, ensure_ascii=False))

    def _error(self, response):
        # Keep only quota/retry diagnostics, not arbitrary upstream messages.
        self.quota_diagnostic = None
        if response.status_code == 429:
            try:
                details = response.json().get('error', {}).get('details', [])
                self.quota_diagnostic = [{'retry_delay': item['retryDelay']} for item in details
                    if isinstance(item, dict) and re.fullmatch(r'[0-9.]+s', str(item.get('retryDelay', '')))]
            except (ValueError, TypeError, AttributeError):
                pass
        return super()._error(response)

    def _failed(self, reason, diagnostic=None):
        result = super()._failed(reason, diagnostic)
        if reason == 'api_rate_limited' and getattr(self, 'quota_diagnostic', None):
            result['quota_diagnostic'] = self.quota_diagnostic
        return result
