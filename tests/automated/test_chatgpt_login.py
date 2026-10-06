"""Separate login routes must never delegate to the training provider."""
from fastapi.testclient import TestClient

from da_agent.app import create_app


class GeminiTraining:
    def status(self):
        return {"provider": "gemini", "state": "ready", "message": "Gemini API"}

    def __getattr__(self, name):
        raise AssertionError(f"Login must not call Gemini.{name}")


class LoginProbe:
    def __init__(self):
        self.calls = []

    def status(self):
        return {"state": "connected", "connected": True, "plan_enabled": True}

    def start(self):
        self.calls.append("start")
        return {"authorization_url": "https://auth.openai.com/api/accounts/authorize?state=test"}

    def callback(self, **kwargs):
        self.calls.append(kwargs)

    def disconnect(self):
        self.calls.append("disconnect")
        return {"state": "disconnected"}


def test_gemini_and_chatgpt_login_are_independent():
    training, login = GeminiTraining(), LoginProbe()
    app = create_app(auth=training, chatgpt_auth=login)
    # No lifespan: these routes do not require PostgreSQL or model calls.
    client = TestClient(app)
    assert app.state.auth is training
    assert app.state.training.auth is training
    assert client.get("/api/auth/status").json()["provider"] == "gemini"
    assert client.get("/api/chatgpt/status").json()["plan_enabled"] is True
    assert client.post("/api/chatgpt/start", json={}).status_code == 200
    response = client.get("/auth/callback?code=code&state=test&client_id=oaiapp_test", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/?auth=returned"
    assert login.calls[-1] == dict(code="code", state="test", client_id="oaiapp_test", error=None)
    assert client.post("/api/chatgpt/disconnect", json={}).json()["state"] == "disconnected"
    assert client.get("/api/auth/status").json()["provider"] == "gemini"


def test_start_failure_and_cross_origin_rejection():
    login = LoginProbe()
    login.start = lambda: {"state": "error", "reason": "network_unavailable"}
    client = TestClient(create_app(auth=GeminiTraining(), chatgpt_auth=login))
    response = client.post("/api/chatgpt/start", json={})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "chatgpt_auth_unavailable"
    assert client.post("/api/chatgpt/start", json={}, headers={"Origin": "https://other.invalid"}).status_code == 403
