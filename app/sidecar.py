"""Client for the Microsoft Entra ID Auth SDK sidecar.

The sidecar acquires T1 for the agent app id, then exchanges Tc and T1
for Tr. The OBO client id is the blueprint. This client sends Tc in and
receives Tr back. It never asks for T1 and never talks to Entra ID.
"""

from typing import Any

import httpx

from app.config import Settings
from app.redact import redact

_TIMEOUT = httpx.Timeout(30.0, connect=2.0)


class SidecarError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class SidecarUnavailable(SidecarError):
    def __init__(self) -> None:
        super().__init__(503, "auth sidecar is unavailable")


class SidecarClient:
    def __init__(self, settings: Settings, http: httpx.Client | None = None) -> None:
        self._settings = settings
        self._http = http or httpx.Client(timeout=_TIMEOUT, follow_redirects=False)
        self._owns_client = http is None

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def healthy(self) -> bool:
        try:
            response = self._http.get(f"{self._settings.sidecar_url}/healthz")
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    def validate_tc(self, tc: str) -> dict[str, Any]:
        """Ask the sidecar to validate the inbound user token Tc."""
        response = self._request(
            "GET",
            "/Validate",
            headers={"Authorization": f"Bearer {tc}"},
        )
        body = _json_object(response)
        claims = body.get("claims")
        if not isinstance(claims, dict):
            raise SidecarError(502, "sidecar validation response did not include claims")
        return claims

    def exchange_tc_for_tr(self, tc: str) -> str:
        """Exchange Tc for a gateway Tr through the sidecar.

        The sidecar validates Tc (audience: blueprint app id), acquires T1
        for AgentIdentity (the agent app id), then requests Tr with
        client id blueprint, assertion=Tc, and client_assertion=T1.
        """
        service = self._settings.downstream_service
        response = self._request(
            "GET",
            f"/AuthorizationHeader/{service}",
            params={"AgentIdentity": self._settings.agent_client_id},
            headers={"Authorization": f"Bearer {tc}"},
        )
        header = _json_object(response).get("authorizationHeader")
        if not isinstance(header, str) or not header.startswith("Bearer "):
            raise SidecarError(502, "sidecar did not return a Bearer authorization header")
        return header

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._http.request(method, f"{self._settings.sidecar_url}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise SidecarUnavailable() from exc
        if response.status_code != 200:
            raise SidecarError(response.status_code, _error_detail(response))
        return response


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise SidecarError(502, "sidecar returned a non-JSON response") from exc
    if not isinstance(body, dict):
        raise SidecarError(502, "sidecar returned an unexpected response")
    return body


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return redact(response.text)
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("title") or "sidecar request failed"
        return redact(str(detail))
    return "sidecar request failed"
