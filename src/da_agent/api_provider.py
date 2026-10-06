"""Explicit API billing provider. Credentials remain in server-side secrets."""
import os
import hashlib
import re
import json
from pathlib import Path
import threading
import uuid

import httpx


class GeminiProvider:
    def __init__(self, *, key_file=None, model=None, http_client=None):
        self.key_file = Path(key_file or os.getenv("GEMINI_API_KEY_FILE", ".local/gemini_api.key"))
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,100}", self.model):
            raise ValueError("Invalid GEMINI_MODEL")
        self.http = http_client or httpx.Client(timeout=90, follow_redirects=False)
        self.lock = threading.RLock()
        self.verified_key = None
        self.last_error = None

    def _key(self):
        # Explicit file configuration never falls through to another credential.
        try:
            return self.key_file.read_text(encoding="utf-8-sig").strip()
        except (OSError, UnicodeError):
            return ""

    def status(self):
        key = self._key()
        configured = bool(key)
        verified = configured and self.verified_key == hashlib.sha256(key.encode()).digest()
        label = 'Gemma API' if self.model.startswith('gemma-') else 'Gemini API'
        return {"state": "ready" if configured else "unavailable", "provider": "gemini",
                "configured": configured, "connected": configured,
                "inference_verified": verified, "model": self.model,
                "reason": self.last_error if configured else "api_key_missing",
                "message": (f"{label} · 실제 호출 확인됨" if verified else f"{label} · 키 설정됨 · 실제 호출 검증 전") if configured else f"{label} · API 키 설정 필요"}

    def _error(self, response):
        try:
            error = response.json().get("error", {})
            if any(item.get("reason") in {"API_KEY_INVALID", "API_KEY_EXPIRED"} for item in error.get("details", []) if isinstance(item, dict)):
                return "api_key_invalid"
        except (ValueError, AttributeError, TypeError):
            pass
        return {401: "api_key_invalid", 403: "api_permission_denied", 404: "model_unavailable", 429: "api_rate_limited"}.get(response.status_code, "api_unavailable" if response.status_code >= 500 else "api_request_invalid")

    def _failed(self, reason, diagnostic=None):
        self.last_error = reason
        if reason in {"api_key_invalid", "api_permission_denied"}:
            self.verified_key = None
        result = {"state": "error", "reason": reason, "model": self.model}
        if diagnostic:
            result['provider_diagnostic'] = diagnostic
        return result

    def research(self, messages):
        selected = os.getenv('GEMINI_SEARCH_MODEL') or self.model
        result = self.review(messages, model=selected, use_search=True)
        result['model'] = selected
        return result

    def select_case(self, messages):
        return self.review(messages)

    def review(self, messages, model=None, *, use_search=False):
        with self.lock:
            key = self._key()
            if not key:
                return self._failed("api_key_missing")
            selected = model or self.model
            diagnostic = dict(request_id=str(uuid.uuid4()), stage='request', timeout=False)
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,100}", selected):
                return self._failed("model_unavailable")
            try:
                system = [{"text": message["content"]} for message in messages if message["role"] in {"developer", "system"}]
                contents = [{"role": "model" if message["role"] == "assistant" else "user", "parts": [{"text": message["content"]}]} for message in messages if message["role"] not in {"developer", "system"}]
                body = {"contents": contents, "systemInstruction": {"parts": system},
                        "generationConfig": {"maxOutputTokens": 4096}, "store": False}
                if use_search:
                    body['tools'] = [{'googleSearch': {}}]
                # Application-owned adaptive design/validation envelopes need JSON
                # syntax enforced by the provider, with semantic checks on the server.
                envelope = None
                for message in messages:
                    if message['role']=='user':
                        try: envelope=json.loads(message['content'])
                        except (ValueError,TypeError): pass
                        break
                if isinstance(envelope,dict) and 'request' in envelope and (
                    isinstance(envelope.get('schema'),dict) and envelope['schema'].get('title')=='Recipe'
                    or {'task','dictionary','private_generation_recipe'}.issubset(envelope)):
                    body['generationConfig']['responseMimeType']='application/json'
                    body['generationConfig']['maxOutputTokens']=8192
                    if isinstance(envelope.get('schema'),dict) and envelope['schema'].get('properties'):
                        def provider_schema(value):
                            if isinstance(value,dict):
                                return {k:({name:provider_schema(child) for name,child in v.items()} if k in ('properties','$defs') else provider_schema(v)) for k,v in value.items() if k not in ('minItems','maxItems','minLength','maxLength','pattern','title','default','description')}
                            if isinstance(value,list): return [provider_schema(v) for v in value]
                            return value
                        body['generationConfig']['responseJsonSchema']=provider_schema(envelope['schema'])
                if isinstance(envelope,dict) and envelope.get('case_selection_version')=='case-selection-v1':
                    body['generationConfig'].update(responseMimeType='application/json',
                        responseJsonSchema=envelope['schema'])
                if isinstance(envelope, dict) and envelope.get('contract_version') == 'coaching-v2' and isinstance(envelope.get('sources'), list):
                    from .coaching import coaching_schema
                    body['generationConfig']['responseMimeType'] = 'application/json'
                    body['generationConfig']['responseJsonSchema'] = coaching_schema(envelope)
                if isinstance(envelope, dict) and envelope.get('interpretation_version') == 'interpretation-v3' and isinstance(envelope.get('capabilities'), list):
                    from .task_planner import interpretation_schema
                    body['generationConfig']['responseMimeType'] = 'application/json'
                    body['generationConfig']['responseJsonSchema'] = interpretation_schema(envelope)
                if isinstance(envelope, dict) and envelope.get('evaluation_version') == 'request-review-v3' and isinstance(envelope.get('rubric'), dict):
                    from .evaluation_v3 import response_schema
                    body['generationConfig']['responseMimeType'] = 'application/json'
                    body['generationConfig']['responseJsonSchema'] = response_schema(envelope)
                    body['generationConfig']['maxOutputTokens'] = 8192
                if selected == "gemini-3.8-flash":
                    body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": "low"}
                if selected.startswith('gemma-4-'):
                    body['generationConfig']['thinkingConfig'] = {'thinkingLevel': 'minimal'}
                    # Gemma's constrained decoder can fail to finish large nested
                    # schemas. The schema remains in the prompt and application
                    # validation remains authoritative; retain JSON output mode.
                    if isinstance(envelope,dict) and isinstance(envelope.get('schema'),dict):
                        body['generationConfig'].pop('responseJsonSchema',None)
                response = self.http.post("https://generativelanguage.googleapis.com/v1beta/models/" + selected + ":generateContent",
                    headers={"x-goog-api-key": key}, json=body)
                if response.status_code >= 400:
                    return self._failed(self._error(response), dict(diagnostic, stage='http', http_status=response.status_code))
                diagnostic.update(stage='response', http_status=response.status_code)
                data = response.json()
                if data.get("promptFeedback", {}).get("blockReason"):
                    return self._failed("api_content_blocked", diagnostic)
                candidates = data.get("candidates", [])
                if not candidates:
                    return self._failed("api_empty_response", diagnostic)
                candidate = candidates[0]
                if candidate.get("finishReason") != "STOP":
                    return self._failed("api_content_blocked" if candidate.get("finishReason") in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"} else "response_incomplete", diagnostic)
                text = "".join(part["text"] for part in candidate.get("content", {}).get("parts", [])
                    if isinstance(part.get("text"), str) and not part.get("thought"))
                if not text.strip():
                    return self._failed("api_empty_response", diagnostic)
                self.verified_key, self.last_error = hashlib.sha256(key.encode()).digest(), None
                metadata = data.get('usageMetadata') or {}
                usage = {key: metadata.get(source) for key, source in {'input_tokens': 'promptTokenCount', 'output_tokens': 'candidatesTokenCount', 'total_tokens': 'totalTokenCount'}.items()}
                result = {"state": "completed", "text": text, "model": selected}
                if use_search:
                    result['grounding'] = candidate.get('groundingMetadata') or {}
                if metadata:
                    result['usage'] = usage
                return result
            except httpx.TimeoutException:
                return self._failed("api_timeout", dict(diagnostic, timeout=True, stage='transport'))
            except httpx.HTTPError:
                return self._failed("api_unavailable", dict(diagnostic, stage='transport'))
            except (ValueError, TypeError, KeyError, AttributeError):
                return self._failed("api_invalid_response", diagnostic)

    def models(self):
        key = self._key()
        if not key:
            return self._failed("api_key_missing")
        try:
            response = self.http.get("https://generativelanguage.googleapis.com/v1beta/models/" + self.model, headers={"x-goog-api-key": key})
            if response.status_code >= 400:
                return self._failed(self._error(response))
            data = response.json()
            if "generateContent" not in data.get("supportedGenerationMethods", []):
                return self._failed("model_unavailable")
            return {"state": "available", "models": [{"slug": self.model, "display_name": data.get("displayName", self.model)}]}
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
            return self._failed("api_unavailable")

    def start(self):
        return {"state": "error", "reason": "api_local_configuration_required"}

    def callback(self, **kwargs):
        return {"state": "error", "reason": "api_local_configuration_required"}

    def disconnect(self):
        return {**self.status(), "reason": "api_local_configuration_required"}


def configured_provider():
    provider = os.getenv("DA_LLM_PROVIDER", "chatgpt")
    if provider == "gemini":
        return GeminiProvider()
    if provider != "chatgpt":
        raise ValueError("DA_LLM_PROVIDER must be chatgpt or gemini")
    from .auth import AuthService
    return AuthService()
