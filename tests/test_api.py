import base64
import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _jwt(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{body}.sig"


class FakeSidecar:
    def __init__(self, header: str, healthy: bool = True) -> None:
        self.header = header
        self._healthy = healthy
        self.validated: list[str] = []

    def close(self) -> None:
        return None

    def healthy(self) -> bool:
        return self._healthy

    def validate_tc(self, tc: str) -> dict:
        self.validated.append(tc)
        return {"aud": "blueprint-app-id"}

    def exchange_tc_for_tr(self, tc: str) -> str:
        assert tc == "user-token"
        return self.header


class FakeGateway:
    def __init__(self) -> None:
        self.headers: list[str] = []

    def close(self) -> None:
        return None

    def call(self, tr_authorization_header: str) -> dict:
        self.headers.append(tr_authorization_header)
        return {"statusCode": 200, "body": {"ok": True}}


def _settings() -> Settings:
    return Settings(
        sidecar_url="http://127.0.0.1:5000",
        agent_client_id="agent-app-id",
        gateway_base_url="https://gateway.internal",
        gateway_audience="gateway-app-id",
        gateway_path="/whoami",
    )


def test_gateway_requires_tc() -> None:
    with TestClient(create_app(_settings(), FakeSidecar("Bearer x"), FakeGateway())) as client:
        response = client.get("/gateway")
    assert response.status_code == 401


def test_gateway_calls_upstream_only_when_tr_matches() -> None:
    token = _jwt({"azp": "agent-app-id", "aud": "gateway-app-id", "idtyp": "user", "preferred_username": "ada"})
    gateway = FakeGateway()
    sidecar = FakeSidecar(f"Bearer {token}")
    with TestClient(create_app(_settings(), sidecar, gateway)) as client:
        response = client.get("/gateway", headers={"Authorization": "Bearer user-token"})
    assert response.status_code == 200
    body = response.json()
    assert body["trClaims"]["azp"] == "agent-app-id"
    assert body["trClaims"]["aud"] == "gateway-app-id"
    assert "preferred_username" not in body["trClaims"]
    assert token not in response.text
    assert gateway.headers == [f"Bearer {token}"]
    assert sidecar.validated == ["user-token"]


def test_mismatched_azp_does_not_call_the_gateway() -> None:
    token = _jwt({"azp": "someone-else", "aud": "gateway-app-id"})
    gateway = FakeGateway()
    with TestClient(create_app(_settings(), FakeSidecar(f"Bearer {token}"), gateway)) as client:
        response = client.get("/gateway", headers={"Authorization": "Bearer user-token"})
    assert response.status_code == 502
    assert gateway.headers == []
    assert token not in response.text


def test_ready_fails_when_sidecar_is_down() -> None:
    with TestClient(create_app(_settings(), FakeSidecar("Bearer x", healthy=False), FakeGateway())) as client:
        response = client.get("/ready")
    assert response.status_code == 503
