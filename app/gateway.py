"""Call the gateway with Tr. Redirects stay off so Tr cannot leave that host."""

from typing import Any

import httpx

from app.config import Settings
from app.redact import redact

_TIMEOUT = httpx.Timeout(15.0, connect=2.0)
_MAX_BODY = 65_536


class GatewayError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class GatewayClient:
    def __init__(self, settings: Settings, http: httpx.Client | None = None) -> None:
        self._settings = settings
        self._http = http or httpx.Client(timeout=_TIMEOUT, follow_redirects=False)
        self._owns_client = http is None

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def call(self, tr_authorization_header: str) -> dict[str, Any]:
        url = f"{self._settings.gateway_base_url}{self._settings.gateway_path}"
        try:
            response = self._http.get(
                url,
                headers={
                    "Authorization": tr_authorization_header,
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise GatewayError(503, "gateway is unavailable") from exc
        if response.status_code != 200:
            raise GatewayError(response.status_code, _safe_error(response))
        return {"statusCode": response.status_code, "body": _safe_body(response)}


def _safe_error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return redact(response.text)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("code"):
            return redact(str(error["code"]))
        if body.get("title"):
            return redact(str(body["title"]))
    return "gateway rejected the request"


def _safe_body(response: httpx.Response) -> Any:
    raw = response.content[:_MAX_BODY]
    try:
        body = response.json()
    except ValueError:
        return redact(raw.decode("utf-8", errors="replace"))
    return _redact_json(body)


def _redact_json(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_redact_json(item) for item in value[:50]]
    if isinstance(value, dict):
        return {str(key): _redact_json(item) for key, item in list(value.items())[:50]}
    return value
