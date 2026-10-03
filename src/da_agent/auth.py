"""Official Sign in with ChatGPT OSS adapter; never reads Codex credentials.

Single-process local runtime. All returned dictionaries are safe for the UI;
credential storage is encrypted with a separately provisioned Fernet key.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
from typing import Any
from urllib.parse import urlencode, urlparse
import uuid

import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"


class AuthFailure(Exception):
    """Contains a static/sanitized public code, never upstream body or tokens."""


class AuthService:
    def __init__(self, *, storage_path: str | Path | None = None,
                 encryption_key: str | bytes | None = None,
                 redirect_uri: str | None = None,
                 client_id: str | None = None,
                 http_client: httpx.Client | None = None):
        self.path = Path(storage_path or os.getenv("DA_AUTH_STORAGE_PATH", "runtime/auth/credentials.enc"))
        self.redirect_uri = redirect_uri or os.getenv("DA_AUTH_REDIRECT_URI", "http://127.0.0.1:8000/auth/callback")
        self.initial_client_id = client_id or os.getenv("DA_AUTH_CLIENT_ID", "dynamic_agent_client")
        self.http = http_client or httpx.Client(timeout=60, follow_redirects=False)
        self.lock = threading.RLock()
        self.pending: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.metadata: dict[str, Any] | None = None
        self.data: dict[str, Any] = {"host_id": "urn:uuid:" + str(uuid.uuid4()), "accounts": {}, "active": None}
        self.cipher: Fernet | None = None
        self.unavailable_reason: str | None = None
        parsed = urlparse(self.redirect_uri)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.path or parsed.query or parsed.fragment or parsed.username:
            self.unavailable_reason = "invalid_loopback_callback"
            return
        try:
            key = encryption_key or os.getenv("DA_AUTH_ENCRYPTION_KEY")
            key_file = os.getenv("DA_AUTH_ENCRYPTION_KEY_FILE")
            if not key and key_file:
                key = Path(key_file).read_bytes().strip()
            if not key:
                self.unavailable_reason = "auth_storage_key_required"
                return
            self.cipher = Fernet(key.encode() if isinstance(key, str) else key)
            if self.path.exists():
                self.data = json.loads(self.cipher.decrypt(self.path.read_bytes()))
                if not isinstance(self.data.get("accounts"), dict) or not str(self.data.get("host_id", "")).startswith("urn:uuid:"):
                    raise ValueError("Invalid credential record")
            else:
                self._save()
        except (OSError, ValueError, TypeError, InvalidToken):
            # An unreadable store must not silently rotate this host's identity.
            self.cipher = None
            self.unavailable_reason = "auth_storage_unavailable"

    def _save(self) -> None:
        if not self.cipher:
            raise AuthFailure("auth_storage_key_required")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = self.cipher.encrypt(json.dumps(self.data, ensure_ascii=False).encode())
        fd, temporary = tempfile.mkstemp(prefix=".auth-", dir=self.path.parent)
        try:
            if os.name != "nt":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _account(self) -> dict[str, Any] | None:
        return self.data["accounts"].get(self.data.get("active"))

    def status(self) -> dict[str, Any]:
        with self.lock:
            account = self._account()
            connected = bool(account and account.get("access_token"))
            plan = connected and PLAN_SCOPE in account.get("scopes", [])
            return {"state": "unavailable" if self.unavailable_reason else ("connected" if connected else "disconnected"),
                    "connected": connected, "plan_enabled": bool(plan),
                    "inference_verified": bool(account and account.get("inference_verified")),
                    "reason": self.unavailable_reason or self.last_error,
                    "account": {"email": account.get("email"), "subject": account["subject"], "registration_id": self.data["active"]} if account else None,
                    "accounts": [{"registration_id": key, "email": value.get("email"), "subject": value["subject"]} for key, value in self.data["accounts"].items()],
                    "usage_url": "https://chatgpt.com/settings/usage"}

    def _discovery(self) -> dict[str, Any]:
        if self.metadata is None:
            metadata = self._json(self.http.get(ISSUER + "/.well-known/openid-configuration"))
            if metadata.get("issuer") != ISSUER:
                raise AuthFailure("invalid_issuer_metadata")
            for field in ("authorization_endpoint", "token_endpoint", "jwks_uri", "revocation_endpoint"):
                endpoint = urlparse(metadata.get(field, ""))
                if endpoint.scheme != "https" or endpoint.hostname != "auth.openai.com":
                    raise AuthFailure("invalid_issuer_metadata")
            self.metadata = metadata
        return self.metadata

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            try:
                body = response.json()
                upstream = body.get("error") if isinstance(body, dict) else None
                code = upstream.get("code") if isinstance(upstream, dict) else upstream
                if code in ("invalid_grant", "invalid_token"):
                    raise AuthFailure("reauthorization_required")
            except ValueError:
                pass
            codes = {401: "reauthorization_required", 403: "plan_permission_denied", 429: "usage_limit_exceeded"}
            raise AuthFailure(codes.get(response.status_code, "upstream_request_failed"))
        try:
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except ValueError:
            raise AuthFailure("invalid_upstream_response") from None

    def start(self, *, registration_id: str | None = None, new_account: bool = False) -> dict[str, Any]:
        with self.lock:
            if self.unavailable_reason:
                return {"state": "unavailable", "reason": self.unavailable_reason}
            try:
                metadata = self._discovery()
                selected = None if new_account else (registration_id or self.data.get("active"))
                account = self.data["accounts"].get(selected)
                if selected and not account:
                    raise AuthFailure("unknown_account")
                client = account["client_id"] if account else self.initial_client_id
                verifier = secrets.token_urlsafe(48)
                state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                self.pending = {"state": state, "nonce": nonce, "verifier": verifier,
                                "client_id": client, "selected": selected, "expires_at": time.time() + 600,
                                "redirect_uri": self.redirect_uri}
                params = {"client_id": client, "ext_agent_host_id": self.data["host_id"], "response_type": "code",
                          "redirect_uri": self.redirect_uri, "scope": SCOPES, "resource": RESOURCE, "state": state,
                          "nonce": nonce, "code_challenge_method": "S256",
                          "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")}
                if client == "dynamic_agent_client":
                    params["agent_name_hint"] = "DA-Agent"
                if account and account.get("id_token"):
                    params["id_token_hint"] = account["id_token"]
                if account and account.get("email"):
                    params["login_hint"] = account["email"]
                self.last_error = None
                # Do not log or persist this URL: a returning sign-in can contain an ID token hint.
                return {"state": "authorization_pending", "authorization_url": metadata["authorization_endpoint"] + "?" + urlencode(params)}
            except (AuthFailure, httpx.HTTPError) as exc:
                return self._error(exc)

    def _error(self, error: Exception) -> dict[str, Any]:
        self.last_error = str(error) if isinstance(error, AuthFailure) else "network_unavailable"
        return {"state": "error", "reason": self.last_error, "retryable": True}

    def callback(self, code: str | None = None, state: str | None = None,
                 client_id: str | None = None, error: str | None = None) -> dict[str, Any]:
        with self.lock:
            attempt, self.pending = self.pending, None  # Consume once, including rejected callbacks.
            try:
                if not attempt or not state or not secrets.compare_digest(state, attempt["state"]) or attempt["expires_at"] <= time.time():
                    raise AuthFailure("invalid_authorization_state")
                if error:
                    raise AuthFailure("authorization_denied")
                if not code:
                    raise AuthFailure("authorization_code_missing")
                issued = client_id or attempt["client_id"]
                if issued == "dynamic_agent_client":
                    raise AuthFailure("registration_incomplete")
                if attempt["client_id"] != "dynamic_agent_client" and issued != attempt["client_id"]:
                    raise AuthFailure("registration_mismatch")
                metadata = self._discovery()
                tokens = self._json(self.http.post(metadata["token_endpoint"], data={"grant_type": "authorization_code", "code": code,
                                   "code_verifier": attempt["verifier"], "client_id": issued,
                                   "redirect_uri": attempt["redirect_uri"], "resource": RESOURCE}))
                identity = self._validate_identity(tokens.get("id_token", ""), issued, attempt["nonce"])
                old = self.data["accounts"].get(attempt["selected"])
                if old and old["subject"] != identity["sub"]:
                    raise AuthFailure("account_identity_mismatch")
                account = {"subject": identity["sub"], "email": identity.get("email"), "issuer": ISSUER,
                           "client_id": issued, "id_token": tokens["id_token"], "inference_verified": False}
                self._apply_tokens(account, tokens)
                key = issued + ":" + identity["sub"]
                self.data["accounts"][key] = account
                self.data["active"] = key
                self._save()
                self.last_error = None
                return self.status()
            except (AuthFailure, httpx.HTTPError, OSError) as exc:
                return self._error(exc)

    def _validate_identity(self, token: str, audience: str, nonce: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in ("RS256", "ES256"):
                raise ValueError()
            jwks = self._json(self.http.get(self._discovery()["jwks_uri"]))
            candidates = [key for key in jwks.get("keys", []) if key.get("kid") == header.get("kid")]
            if len(candidates) != 1:
                raise ValueError()
            key = jwt.PyJWK.from_dict(candidates[0], algorithm=header["alg"]).key
            identity = jwt.decode(token, key, algorithms=[header["alg"]], audience=audience, issuer=ISSUER,
                                  leeway=5, options={"require": ["sub", "exp", "iat", "nonce"]})
            if not isinstance(identity["sub"], str) or not identity["sub"] or not secrets.compare_digest(identity["nonce"], nonce):
                raise ValueError()
            return identity
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise AuthFailure("invalid_identity_token") from None

    @staticmethod
    def _apply_tokens(account: dict[str, Any], tokens: dict[str, Any]) -> None:
        if not isinstance(tokens.get("access_token"), str) or not tokens["access_token"] or tokens.get("token_type", "").lower() != "bearer":
            raise AuthFailure("invalid_token_response")
        try:
            expiry = float(tokens["expires_in"])
            if expiry <= 0:
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise AuthFailure("invalid_token_response") from None
        account.update(access_token=tokens["access_token"], expires_at=time.time() + expiry,
                       scopes=str(tokens.get("scope", "")).split())
        if tokens.get("refresh_token"):
            account["refresh_token"] = tokens["refresh_token"]

    def _bearer(self) -> str:
        account = self._account()
        if not account or not account.get("access_token"):
            raise AuthFailure("sign_in_required")
        if PLAN_SCOPE not in account.get("scopes", []):
            raise AuthFailure("plan_permission_required")
        if account["expires_at"] <= time.time() + 30:
            if not account.get("refresh_token"):
                raise AuthFailure("reauthorization_required")
            tokens = self._json(self.http.post(self._discovery()["token_endpoint"], data={"grant_type": "refresh_token",
                               "client_id": account["client_id"], "refresh_token": account["refresh_token"], "resource": RESOURCE}))
            if not tokens.get("refresh_token"):
                raise AuthFailure("invalid_refresh_response")
            replacement = dict(account)
            self._apply_tokens(replacement, tokens)
            self.data["accounts"][self.data["active"]] = replacement
            self._save()
            account = replacement
            if PLAN_SCOPE not in account["scopes"]:
                raise AuthFailure("plan_permission_required")
        return account["access_token"]

    def models(self) -> dict[str, Any]:
        with self.lock:
            try:
                token = self._bearer()
                result = self._json(self.http.get(RESOURCE + "/models", headers={"Authorization": "Bearer " + token}))
                models = result.get("models")
                if not isinstance(models, list):
                    raise AuthFailure("invalid_model_catalog")
                return {"state": "available", "models": [{"slug": model["slug"], "display_name": model.get("display_name", model["slug"])}
                        for model in models if isinstance(model, dict) and model.get("visibility") == "list" and isinstance(model.get("slug"), str)]}
            except (AuthFailure, httpx.HTTPError, OSError) as exc:
                return self._error(exc)

    def review(self, messages: list[dict[str, Any]], model: str | None = None) -> dict[str, Any]:
        """Input stays in memory; no partial text is returned as a completed review."""
        with self.lock:
            try:
                catalog = self.models()
                if catalog["state"] != "available":
                    return catalog
                choices = [entry["slug"] for entry in catalog["models"]]
                selected = model or (choices[0] if choices else None)
                if selected not in choices:
                    raise AuthFailure("model_unavailable")
                pieces: list[str] = []
                completed = False
                with self.http.stream("POST", RESOURCE + "/responses", headers={"Authorization": "Bearer " + self._bearer()},
                                      json={"model": selected, "input": messages, "store": False, "stream": True}) as response:
                    if response.status_code >= 400:
                        response.read()
                        self._json(response)
                    data_lines: list[str] = []
                    for line in response.iter_lines():
                        if line.startswith("data:"):
                            data_lines.append(line[5:].lstrip())
                        elif line == "" and data_lines:
                            data = "\n".join(data_lines)
                            data_lines.clear()
                            if data == "[DONE]":
                                continue
                            try:
                                event = json.loads(data)
                                if not isinstance(event, dict):
                                    raise ValueError()
                            except ValueError:
                                raise AuthFailure("invalid_response_stream") from None
                            kind = event.get("type")
                            if kind == "response.output_text.delta":
                                pieces.append(str(event.get("delta", "")))
                            elif kind in ("response.failed", "response.incomplete", "error"):
                                response_data = event.get("response") or {}
                                upstream = (response_data.get("error") if isinstance(response_data, dict) else None) or event.get("error") or {}
                                code = upstream.get("code") if isinstance(upstream, dict) else None
                                safe = {"subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable", "subscription_sharing_user_not_eligible"}
                                raise AuthFailure(code if code in safe else ("response_incomplete" if kind == "response.incomplete" else "response_failed"))
                            elif kind == "response.completed":
                                completed = True
                    if not completed:
                        raise AuthFailure("response_interrupted")
                account = self._account()
                account["inference_verified"] = True
                self._save()
                self.last_error = None
                return {"state": "completed", "text": "".join(pieces), "model": selected}
            except (AuthFailure, httpx.HTTPError, OSError) as exc:
                return self._error(exc)

    def disconnect(self) -> dict[str, Any]:
        with self.lock:
            account = self._account()
            confirmed = False
            self.pending = None
            if account and account.get("refresh_token"):
                try:
                    response = self.http.post(self._discovery()["revocation_endpoint"], data={"token": account["refresh_token"],
                                              "token_type_hint": "refresh_token", "client_id": account["client_id"]})
                    confirmed = response.status_code == 200
                except (AuthFailure, httpx.HTTPError):
                    pass
            if account:
                for field in ("access_token", "refresh_token", "id_token", "expires_at", "scopes"):
                    account.pop(field, None)
                account["inference_verified"] = False
                self._save()
            self.last_error = None if confirmed or not account else "remote_revocation_unconfirmed"
            return {**self.status(), "remote_revocation_confirmed": confirmed}
