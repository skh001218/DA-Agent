"""Offline tests with actual RSA signatures and mocked official HTTP endpoints."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt

from da_agent.auth import AuthService, ISSUER, SCOPES


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "credentials.enc"
        self.key = Fernet.generate_key()
        self.signing = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.requests = []
        self.claim_overrides = {}
        self.scopes = SCOPES
        self.fail_revoke = False
        self.bad_signature = False
        self.refresh_failure = False
        self.events = [{"type": "response.output_text.delta", "delta": "검토 완료"}, {"type": "response.completed"}]
        self.service = AuthService(storage_path=self.path, encryption_key=self.key,
                                   http_client=httpx.Client(transport=httpx.MockTransport(self.handle)))

    def tearDown(self):
        self.temp.cleanup()

    def handle(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={"issuer": ISSUER, "authorization_endpoint": ISSUER + "/api/accounts/authorize",
                "token_endpoint": ISSUER + "/api/accounts/oauth/token", "jwks_uri": ISSUER + "/.well-known/jwks.json",
                "revocation_endpoint": ISSUER + "/oauth/revoke"})
        if path == "/.well-known/jwks.json":
            public_key = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key() if self.bad_signature else self.signing.public_key()
            key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(public_key))
            key["kid"] = "test-key"
            return httpx.Response(200, json={"keys": [key]})
        if path == "/api/accounts/oauth/token":
            form = parse_qs(request.content.decode())
            if self.refresh_failure and form["grant_type"] == ["refresh_token"]:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "secret-refresh-new"})
            claims = {"iss": ISSUER, "aud": form["client_id"][0], "sub": "user-one",
                      "iat": int(time.time()), "exp": int(time.time()) + 3600,
                      "nonce": self.nonce, "email": "user@example.test", **self.claim_overrides}
            token = jwt.encode(claims, self.signing, algorithm="RS256", headers={"kid": "test-key"})
            return httpx.Response(200, json={"access_token": "secret-access", "refresh_token": "secret-refresh-new",
                "id_token": token, "token_type": "Bearer", "expires_in": 3600, "scope": self.scopes})
        if path == "/v1/models":
            return httpx.Response(200, json={"models": [{"slug": "account-model", "display_name": "Account model", "visibility": "list"},
                                                       {"slug": "hidden", "visibility": "hidden"}]})
        if path == "/v1/responses":
            body = "".join("data: " + json.dumps(event) + "\n\n" for event in self.events)
            return httpx.Response(200, content=body, headers={"Content-Type": "text/event-stream"})
        if path == "/oauth/revoke":
            return httpx.Response(503 if self.fail_revoke else 200)
        raise AssertionError(str(request.url))

    def begin(self, **kwargs):
        result = self.service.start(**kwargs)
        params = parse_qs(urlparse(result["authorization_url"]).query)
        self.nonce = params["nonce"][0]
        return params

    def login(self):
        params = self.begin()
        return self.service.callback("test-code", params["state"][0], client_id="oaiapp_test")

    def test_dynamic_registration_encryption_and_restart(self):
        params = self.begin()
        self.assertEqual(params["client_id"], ["dynamic_agent_client"])
        self.assertEqual(params["agent_name_hint"], ["DA-Agent"])
        result = self.service.callback("test-code", params["state"][0], client_id="oaiapp_test")
        self.assertTrue(result["plan_enabled"])
        self.assertFalse(result["inference_verified"])
        self.assertNotIn(b"secret-access", self.path.read_bytes())
        self.assertNotIn("secret-access", json.dumps(result))
        restored = AuthService(storage_path=self.path, encryption_key=self.key)
        self.assertEqual(restored.data["host_id"], self.service.data["host_id"])
        self.assertTrue(restored.status()["connected"])
        self.assertEqual(self.service.models()["models"], [{"slug": "account-model", "display_name": "Account model"}])

    def test_state_mismatch_consumed_without_code_exchange(self):
        params = self.begin()
        result = self.service.callback("test-code", "wrong-state", "oaiapp_test")
        self.assertEqual(result["reason"], "invalid_authorization_state")
        self.assertEqual(self.service.callback("test-code", params["state"][0], "oaiapp_test")["reason"], "invalid_authorization_state")
        self.assertFalse(any(r.url.path.endswith("/token") for r in self.requests))

    def test_missing_issued_id_denied_and_expired(self):
        params = self.begin()
        self.assertEqual(self.service.callback("test-code", params["state"][0])["reason"], "registration_incomplete")
        params = self.begin()
        self.assertEqual(self.service.callback(state=params["state"][0], error="access_denied")["reason"], "authorization_denied")
        self.begin()
        state = self.service.pending["state"]
        self.service.pending["expires_at"] = time.time() - 1
        self.assertEqual(self.service.callback("code", state, "oaiapp_test")["reason"], "invalid_authorization_state")

    def test_id_token_validation(self):
        for override in ({"nonce": "wrong"}, {"iss": "https://attacker.invalid"}, {"aud": "other-client"},
                         {"exp": int(time.time()) - 60}):
            with self.subTest(override=override):
                self.claim_overrides = override
                self.assertEqual(self.login()["reason"], "invalid_identity_token")
                self.assertFalse(self.service.status()["connected"])

    def test_invalid_signature_is_rejected(self):
        self.bad_signature = True
        self.assertEqual(self.login()["reason"], "invalid_identity_token")
        self.assertFalse(self.service.status()["connected"])

    def test_returning_registration_mismatch_keeps_active_credentials(self):
        self.login()
        params = self.begin()
        self.assertEqual(params["client_id"], ["oaiapp_test"])
        self.assertNotIn("agent_name_hint", params)
        self.assertIn("id_token_hint", params)
        result = self.service.callback("test-code", params["state"][0], client_id="oaiapp_another")
        self.assertEqual(result["reason"], "registration_mismatch")
        self.assertTrue(self.service.status()["connected"])
        params = self.begin()
        self.claim_overrides = {"sub": "different-user"}
        self.assertEqual(self.service.callback("code", params["state"][0])["reason"], "account_identity_mismatch")
        self.assertEqual(self.service.status()["account"]["subject"], "user-one")

    def test_identity_does_not_enable_plan(self):
        self.scopes = "openid profile email"
        result = self.login()
        self.assertTrue(result["connected"])
        self.assertFalse(result["plan_enabled"])
        self.assertEqual(self.service.models()["reason"], "plan_permission_required")

    def test_completed_response_and_preview_contract(self):
        self.login()
        messages = [{"role": "developer", "content": "Review grounded evidence"}, {"role": "user", "content": "review"}]
        result = self.service.review(messages)
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["text"], "검토 완료")
        payload = json.loads(next(r.content for r in self.requests if r.url.path == "/v1/responses"))
        self.assertFalse(payload["store"])
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["model"], "account-model")
        self.assertEqual(payload["input"], messages)
        self.assertEqual(set(payload), {"model", "input", "store", "stream"})
        self.assertTrue(self.service.status()["inference_verified"])

    def test_failed_partial_and_interrupted_never_completed(self):
        self.login()
        for event, reason in ((None, "response_interrupted"),
                             ({"type": "response.incomplete"}, "response_incomplete"),
                             ({"type": "response.failed", "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}},
                              "subscription_sharing_usage_limit_exceeded")):
            self.events = [{"type": "response.output_text.delta", "delta": "partial"}] + ([event] if event else [])
            result = self.service.review([{"role": "user", "content": "review"}])
            self.assertEqual(result["reason"], reason)
            self.assertNotIn("text", result)

    def test_refresh_rotates_and_disconnect_preserves_registration(self):
        self.login()
        account = self.service._account()
        account["expires_at"] = time.time() - 1
        account["refresh_token"] = "secret-refresh-old"
        self.service.models()
        form = parse_qs(next(r.content.decode() for r in reversed(self.requests) if r.url.path.endswith("/token")))
        self.assertEqual(form["grant_type"], ["refresh_token"])
        self.assertNotIn("scope", form)
        self.assertEqual(self.service._account()["refresh_token"], "secret-refresh-new")
        self.fail_revoke = True
        result = self.service.disconnect()
        self.assertFalse(result["connected"])
        self.assertEqual(result["reason"], "remote_revocation_unconfirmed")
        self.assertNotIn("secret-access", Fernet(self.key).decrypt(self.path.read_bytes()).decode())
        params = self.begin()
        self.assertEqual(params["client_id"], ["oaiapp_test"])
        self.assertNotIn("id_token_hint", params)

    def test_expired_token_scope_downgrade_prevents_inference(self):
        self.login()
        self.service._account()["expires_at"] = time.time() - 1
        self.scopes = "openid profile email"
        result = self.service.review([{"role": "user", "content": "review"}])
        self.assertEqual(result["reason"], "plan_permission_required")
        self.assertFalse(self.service.status()["plan_enabled"])
        self.assertFalse(any(request.url.path == "/v1/responses" for request in self.requests))
        restored = AuthService(storage_path=self.path, encryption_key=self.key)
        self.assertFalse(restored.status()["plan_enabled"])

    def test_invalid_refresh_requires_reauthorization_and_redacts_description(self):
        self.login()
        self.service._account()["expires_at"] = time.time() - 1
        self.refresh_failure = True
        result = self.service.models()
        self.assertEqual(result["reason"], "reauthorization_required")
        self.assertNotIn("secret-refresh", json.dumps(result))

    def test_missing_key_and_invalid_callback_fail_closed(self):
        service = AuthService(storage_path=self.path, redirect_uri="http://localhost:8000/auth/callback", encryption_key=self.key)
        self.assertEqual(service.start()["reason"], "invalid_loopback_callback")
        service = AuthService(storage_path=self.path, encryption_key=Fernet.generate_key())
        self.assertEqual(service.status()["reason"], "auth_storage_unavailable")
        with patch.dict("os.environ", {}, clear=True):
            service = AuthService(storage_path=Path(self.temp.name) / "new.enc")
            self.assertEqual(service.start()["reason"], "auth_storage_key_required")
            self.assertFalse(service.path.exists())


if __name__ == "__main__":
    unittest.main()
