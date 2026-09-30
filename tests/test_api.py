import base64
import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _jwt(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{body}.sig"


class FakeSidecar:
    def __init__(self, header: str, healthy: bool = True, ta_azp: str = "acp-gateway-app-id") -> None:
        self.header = header
        self._healthy = healthy
        self.ta_azp = ta_azp
        self.validated: list[str] = []
        self.exchanged: list[str] = []

    def close(self) -> None:
        return None

    def healthy(self) -> bool:
        return self._healthy

    def validate_ta(self, ta: str) -> dict:
        self.validated.append(ta)
        return {
            "aud": "blueprint-app-id",
            "azp": self.ta_azp,
            "preferred_username": "ada@contoso.example",
        }

    def exchange_ta_for_tr(self, ta: str) -> str:
        assert ta == "actor-token"
        self.exchanged.append(ta)
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
        blueprint_app_id="blueprint-app-id",
        agent_client_id="agent-app-id",
        acp_gateway_app_id="acp-gateway-app-id",
        gateway_base_url="https://gateway.internal",
        gateway_audience="gateway-app-id",
        gateway_path="/whoami",
    )


def test_gateway_requires_ta() -> None:
    with TestClient(create_app(_settings(), FakeSidecar("Bearer x"), FakeGateway())) as client:
        response = client.get("/gateway")
    assert response.status_code == 401


def test_gateway_calls_upstream_only_when_tr_matches() -> None:
    token = _jwt({"azp": "agent-app-id", "aud": "gateway-app-id", "idtyp": "user", "preferred_username": "ada"})
    gateway = FakeGateway()
    sidecar = FakeSidecar(f"Bearer {token}")
    with TestClient(create_app(_settings(), sidecar, gateway)) as client:
        response = client.get("/gateway", headers={"Authorization": "Bearer actor-token"})
    assert response.status_code == 200
    body = response.json()
    assert body["taClaims"]["aud"] == "blueprint-app-id"
    assert body["taClaims"]["azp"] == "acp-gateway-app-id"
    assert "preferred_username" not in body["taClaims"]
    assert body["trClaims"]["azp"] == "agent-app-id"
    assert body["trClaims"]["aud"] == "gateway-app-id"
    assert token not in response.text
    assert gateway.headers == [f"Bearer {token}"]
    assert sidecar.validated == ["actor-token"]


def test_ta_from_another_gateway_is_rejected_before_exchange() -> None:
    sidecar = FakeSidecar("Bearer unused", ta_azp="some-other-app")
    gateway = FakeGateway()
    with TestClient(create_app(_settings(), sidecar, gateway)) as client:
        response = client.get("/gateway", headers={"Authorization": "Bearer actor-token"})
    assert response.status_code == 403
    assert sidecar.exchanged == []
    assert gateway.headers == []


def test_mismatched_azp_does_not_call_the_gateway() -> None:
    token = _jwt({"azp": "someone-else", "aud": "gateway-app-id"})
    gateway = FakeGateway()
    with TestClient(create_app(_settings(), FakeSidecar(f"Bearer {token}"), gateway)) as client:
        response = client.get("/gateway", headers={"Authorization": "Bearer actor-token"})
    assert response.status_code == 502
    assert gateway.headers == []
    assert token not in response.text


def test_ready_fails_when_sidecar_is_down() -> None:
    with TestClient(create_app(_settings(), FakeSidecar("Bearer x", healthy=False), FakeGateway())) as client:
        response = client.get("/ready")
    assert response.status_code == 503
