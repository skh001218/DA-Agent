"""Explicit Gemma model for Discord; keep the running web provider untouched."""
import json
import re
import copy
import hashlib
import os
from pathlib import Path
import uuid

import httpx

from .api_provider import GeminiProvider


class _GemmaClient:
    def __init__(self, client):
        self.client = client
        self.last_metadata = {}

    def post(self, url, **kwargs):
        body = copy.deepcopy(kwargs['json'])
        if '/models/gemma-4-' in url:
            body.setdefault('generationConfig', {})['thinkingConfig'] = {'thinkingLevel': 'minimal'}
            try:
                envelope = json.loads(body['contents'][0]['parts'][0]['text'])
            except (ValueError, TypeError, KeyError, IndexError):
                envelope = None
            if len(body.get('contents', [])) > 1 and isinstance(envelope, dict) and envelope.get('schema', {}).get('title') == 'Recipe':
                # Keep recipe correction as one structured request. Gemma's
                # JSON-mode multi-turn correction can return HTTP 500; previous
                # drafts remain data and the original request/schema stay intact.
                envelope['rejected_design_history'] = body['contents'][1:]
                body['contents'] = [{'role':'user','parts':[{'text':json.dumps(envelope,ensure_ascii=False)}]}]
                body['systemInstruction']['parts'].append({'text':
                    'rejected_design_history는 이전 실패 초안과 검증 오류 자료입니다. 마지막 오류를 수정하되 원래 request와 schema를 유지한 전체 JSON 객체를 반환하세요.'})
        self.last_metadata = {'generation_config':body.get('generationConfig', {})}
        response = self.client.post(url, **dict(kwargs, json=body))
        self.last_metadata['http_status'] = response.status_code
        try:
            candidate = (response.json().get('candidates') or [{}])[0]
            self.last_metadata.update(finish_reason=candidate.get('finishReason'),
                part_count=len(candidate.get('content', {}).get('parts', [])))
        except (ValueError, TypeError, AttributeError, IndexError):
            pass
        return response

    def get(self, *args, **kwargs):
        return self.client.get(*args, **kwargs)


class DiscordGemmaProvider(GeminiProvider):
    generation_mode = 'synthetic'

    def __init__(self, *, key_file, model='gemma-4-26b-a4b-it', http_client=None, diagnostics_directory=None):
        if not model.startswith('gemma-'):
            raise ValueError('Discord Gemma model must start with gemma-')
        super().__init__(key_file=key_file, model=model, http_client=_GemmaClient(http_client or httpx.Client(timeout=90,follow_redirects=False)))
        directory = diagnostics_directory or os.getenv('DISCORD_JSON_DIAGNOSTICS_DIR')
        self.diagnostics_directory = Path(directory) if directory else None

    def research(self,messages):
        # Discord tasks are designed directly by Gemma, without external search.
        return self._failed('research_unavailable')

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
        parsed = None
        try:
            parsed = json.loads(body)
            # Normalize only the unambiguous one-object envelope observed from
            # Gemma. Recipe/schema/quality validation still runs afterwards.
            if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], dict):
                parsed = parsed[0]
                response = dict(response, normalized_envelope='single_object_array')
            if not isinstance(parsed, dict):
                raise ValueError('Object required')
        except (ValueError, TypeError) as exc:
            diagnostic = {'kind':'json_syntax' if isinstance(exc, json.JSONDecodeError) else 'not_object',
                'message':str(exc), 'characters':len(body),
                'body_sha256':hashlib.sha256(body.encode('utf-8')).hexdigest(),
                'fence_removed':bool(fenced)}
            if isinstance(exc, json.JSONDecodeError):
                diagnostic.update(line=exc.lineno, column=exc.colno, position=exc.pos)
            else:
                diagnostic['parsed_type'] = type(parsed).__name__
            if self.diagnostics_directory:
                diagnostic['diagnostic_id'] = uuid.uuid4().hex
                try:
                    self.diagnostics_directory.mkdir(parents=True, exist_ok=True)
                    path = self.diagnostics_directory / (diagnostic['diagnostic_id']+'.json')
                    with path.open('x', encoding='utf-8') as output:
                        os.chmod(path, 0o600)
                        json.dump({'model':response.get('model'), 'diagnostic':diagnostic,
                            'transport':self.http.last_metadata, 'usage':response.get('usage'),
                            'raw_text':response['text'], 'parsed_body':body}, output, ensure_ascii=False, indent=2)
                except OSError:
                    diagnostic['diagnostic_write_failed'] = True
            return dict(response, state='error', reason='provider_invalid_json', text='', json_diagnostic=diagnostic)
        return dict(response, text=json.dumps(parsed, ensure_ascii=False))

    def _error(self, response):
        # Keep only quota/retry diagnostics, not arbitrary upstream messages.
        self.quota_diagnostic = None
        if response.status_code == 429:
            try:
                details = response.json().get('error', {}).get('details', [])
                self.quota_diagnostic = [{'retry_delay': item['retryDelay']} for item in details
                    if isinstance(item, dict) and re.fullmatch(r'[0-9.]+s', str(item.get('retryDelay', '')))]
                for item in details:
                    if not isinstance(item, dict):
                        continue
                    for violation in item.get('violations', []):
                        if isinstance(violation, dict):
                            self.quota_diagnostic.append({key:violation[key] for key in
                                ('quotaMetric','quotaId','quotaValue') if key in violation})
            except (ValueError, TypeError, AttributeError):
                pass
        return super()._error(response)

    def _failed(self, reason, diagnostic=None):
        result = super()._failed(reason, diagnostic)
        if reason == 'api_rate_limited' and getattr(self, 'quota_diagnostic', None):
            result['quota_diagnostic'] = self.quota_diagnostic
        return result
