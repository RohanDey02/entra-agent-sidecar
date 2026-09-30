import base64
import json

import httpx
import pytest

from app.config import Settings
from app.sidecar import SidecarClient, SidecarError


def _settings() -> Settings:
    return Settings(
        sidecar_url="http://127.0.0.1:5000",
        blueprint_app_id="blueprint-app-id",
        agent_client_id="agent-app-id",
        acp_gateway_app_id="acp-gateway-app-id",
        gateway_base_url="https://gateway.internal",
        gateway_audience="gateway-app-id",
        gateway_path="/",
    )


def _jwt(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{body}.sig"


def test_exchange_sends_ta_and_agent_identity_to_authenticated_endpoint() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/Validate":
            return httpx.Response(200, json={"claims": {"aud": "blueprint-app-id"}})
        return httpx.Response(200, json={"authorizationHeader": f"Bearer {_jwt({'azp': 'agent-app-id'})}"})

    client = SidecarClient(_settings(), httpx.Client(transport=httpx.MockTransport(handler)))
    header = client.exchange_ta_for_tr("actor-token")

    assert header.startswith("Bearer ")
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "GET"
    assert request.url.path == "/AuthorizationHeader/Gateway"
    assert request.url.params["AgentIdentity"] == "agent-app-id"
    assert request.headers["Authorization"] == "Bearer actor-token"
    assert "Unauthenticated" not in request.url.path


def test_sidecar_error_redacts_tokens() -> None:
    token = _jwt({"sub": "user"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": f"failed for Bearer {token}"})

    client = SidecarClient(_settings(), httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(SidecarError) as caught:
        client.exchange_ta_for_tr("actor-token")
    assert token not in caught.value.detail
    assert "redacted" in caught.value.detail


def test_redirect_is_not_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://login.microsoftonline.com/token"})

    client = SidecarClient(_settings(), httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(SidecarError) as caught:
        client.exchange_ta_for_tr("actor-token")
    assert caught.value.status_code == 302
