import base64
import json

import pytest

from app.config import (
    ConfigurationError,
    normalize_gateway_base_url,
    normalize_gateway_path,
    normalize_sidecar_url,
)
from app.tokens import TokenShapeError, require_gateway_token, safe_claims


def test_sidecar_url_rejects_public_hosts() -> None:
    with pytest.raises(ConfigurationError):
        normalize_sidecar_url("https://login.microsoftonline.com")
    with pytest.raises(ConfigurationError):
        normalize_sidecar_url("http://sidecar.evil")
    assert normalize_sidecar_url("http://127.0.0.1:5000") == "http://127.0.0.1:5000"
    assert normalize_sidecar_url("http://sidecar:5000/") == "http://sidecar:5000"


def test_gateway_url_must_be_https_and_not_the_sidecar() -> None:
    with pytest.raises(ConfigurationError):
        normalize_gateway_base_url("http://gateway.internal")
    with pytest.raises(ConfigurationError):
        normalize_gateway_base_url("https://127.0.0.1")
    assert normalize_gateway_base_url("https://gateway.internal/api/") == "https://gateway.internal/api"


def test_gateway_path_stays_relative() -> None:
    with pytest.raises(ConfigurationError):
        normalize_gateway_path("https://gateway.internal/admin")
    with pytest.raises(ConfigurationError):
        normalize_gateway_path("/../admin")
    assert normalize_gateway_path("/health") == "/health"


def test_tr_must_name_the_agent_and_the_gateway() -> None:
    claims = {"azp": "agent-app-id", "aud": "gateway-app-id", "scp": "access_as_user"}
    require_gateway_token(claims, agent_client_id="agent-app-id", gateway_audience="gateway-app-id")

    with pytest.raises(TokenShapeError) as caught:
        require_gateway_token(
            {"azp": "blueprint-app-id", "aud": "gateway-app-id"},
            agent_client_id="agent-app-id",
            gateway_audience="gateway-app-id",
        )
    assert "azp" in caught.value.detail

    with pytest.raises(TokenShapeError) as caught:
        require_gateway_token(
            {"azp": "agent-app-id", "aud": "https://graph.microsoft.com"},
            agent_client_id="agent-app-id",
            gateway_audience="gateway-app-id",
        )
    assert "aud" in caught.value.detail


def test_safe_claims_drop_profile_fields() -> None:
    payload = {"azp": "agent-app-id", "aud": "gateway-app-id", "preferred_username": "ada@contoso.example"}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    claims = safe_claims(f"Bearer eyJhbGciOiJub25lIn0.{body}.sig")
    assert claims["azp"] == "agent-app-id"
    assert "preferred_username" not in claims
