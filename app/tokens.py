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
    def __init__(self, detail: str, claims: dict[str, Any], token: str):
        self.detail = detail
        self.claims = claims
        self.token = token
        super().__init__(detail)


def public_claims(claims: dict[str, Any]) -> dict[str, Any]:
    return {name: claims[name] for name in _TR_CLAIMS if name in claims}


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
    return public_claims(payload)


def require_actor_token(
    claims: dict[str, Any],
    *,
    blueprint_app_id: str,
    acp_gateway_app_id: str,
) -> None:
    """Ta is the ACP Gateway token, not the UI login token Tc.

    aud is the blueprint app id. azp is the ACP Gateway app id.
    """
    visible = public_claims(claims)
    if claims.get("aud") != blueprint_app_id:
        raise TokenShapeError("Ta aud did not match the blueprint app id", visible, "ta")
    if claims.get("azp") != acp_gateway_app_id:
        raise TokenShapeError("Ta azp did not match the ACP Gateway app id", visible, "ta")


def require_gateway_token(
    claims: dict[str, Any],
    *,
    agent_client_id: str,
    gateway_audience: str,
) -> None:
    """Tr is minted from Ta + T1 and names the agent as azp."""
    visible = public_claims(claims)
    if claims.get("azp") != agent_client_id:
        raise TokenShapeError("Tr azp did not match the agent app id", visible, "tr")
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if gateway_audience not in audiences:
        raise TokenShapeError("Tr aud did not match the gateway", visible, "tr")
