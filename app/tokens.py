"""Read Tr claims and check that the token is for this agent and gateway."""

import base64
import binascii
import json
from typing import Any

_TR_CLAIMS = (
    "aud",
    "iss",
    "tid",
    "oid",
    "appid",
    "azp",
    "idtyp",
    "scp",
    "xms_par_app_azp",
)


class TokenShapeError(Exception):
    def __init__(self, detail: str, claims: dict[str, Any]):
        self.detail = detail
        self.claims = claims
        super().__init__(detail)


def safe_claims(authorization_header: str) -> dict[str, Any]:
    token = authorization_header.removeprefix("Bearer ").strip()
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError, binascii.Error):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {name: payload[name] for name in _TR_CLAIMS if name in payload}


def require_gateway_token(
    claims: dict[str, Any],
    *,
    agent_client_id: str,
    gateway_audience: str,
) -> None:
    """Tr must name the agent as azp and the gateway as aud.

    T1 never appears here. azp is the agent app id the sidecar requested
    with AgentIdentity. aud is the gateway application, not the blueprint.
    """
    if claims.get("azp") != agent_client_id:
        raise TokenShapeError("Tr azp did not match the agent app id", claims)
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if gateway_audience not in audiences:
        raise TokenShapeError("Tr aud did not match the gateway", claims)
