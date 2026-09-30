"""HTTP API for the ACP Gateway token exchange.

Tc is the UI login token and stops at the ACP Gateway. This app receives Ta.
The sidecar exchanges Ta and T1 for Tr. T1's audience is
api://AzureADTokenExchange, and this process never sees T1.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from app.config import ConfigurationError, Settings
from app.gateway import GatewayClient, GatewayError
from app.sidecar import SidecarClient, SidecarError
from app.tokens import (
    TokenShapeError,
    public_claims,
    require_actor_token,
    require_gateway_token,
    safe_claims,
)

logger = logging.getLogger(__name__)

_FLOW = {
    "tc": "UI login token. The ACP Gateway consumes it and this app does not.",
    "ta": "ACP Gateway token. aud is the blueprint app id and azp is the gateway app id.",
    "t1": "sidecar exchange token. aud is api://AzureADTokenExchange. Not returned here.",
    "tr": "resource token from Ta + T1. azp is the agent app id.",
}


def create_app(
    settings: Settings | None = None,
    sidecar: SidecarClient | None = None,
    gateway: GatewayClient | None = None,
) -> FastAPI:
    try:
        resolved = settings or Settings.from_env()
    except ConfigurationError as exc:
        raise RuntimeError(str(exc)) from exc

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved
        app.state.sidecar = sidecar or SidecarClient(resolved)
        app.state.gateway = gateway or GatewayClient(resolved)
        try:
            yield
        finally:
            app.state.sidecar.close()
            app.state.gateway.close()

    app = FastAPI(title="Entra agent", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.get("/live")
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/ready")
    async def ready(request: Request) -> dict[str, str]:
        healthy = await asyncio.to_thread(request.app.state.sidecar.healthy)
        if not healthy:
            raise HTTPException(status_code=503, detail="auth sidecar is not ready")
        return {"status": "ready"}

    @app.get("/gateway")
    async def call_gateway(request: Request) -> dict[str, Any]:
        ta = _bearer_token(request.headers.get("authorization"))
        broker: SidecarClient = request.app.state.sidecar
        upstream: GatewayClient = request.app.state.gateway
        current: Settings = request.app.state.settings
        try:
            ta_claims = public_claims(await asyncio.to_thread(broker.validate_ta, ta))
            require_actor_token(
                ta_claims,
                blueprint_app_id=current.blueprint_app_id,
                acp_gateway_app_id=current.acp_gateway_app_id,
            )
            tr_header = await asyncio.to_thread(broker.exchange_ta_for_tr, ta)
            tr_claims = safe_claims(tr_header)
            require_gateway_token(
                tr_claims,
                agent_client_id=current.agent_client_id,
                gateway_audience=current.gateway_audience,
            )
            gateway_response = await asyncio.to_thread(upstream.call, tr_header)
        except SidecarError as exc:
            raise _sidecar_http_error(exc) from exc
        except TokenShapeError as exc:
            status = 403 if exc.token == "ta" else 502
            raise HTTPException(
                status_code=status,
                detail={"message": exc.detail, f"{exc.token}Claims": exc.claims},
            ) from exc
        except GatewayError as exc:
            raise HTTPException(status_code=502, detail=exc.detail) from exc
        return {
            "flow": _FLOW,
            "taClaims": ta_claims,
            "trClaims": tr_claims,
            "gateway": gateway_response,
        }

    return app


def _bearer_token(authorization: str | None) -> str:
    if authorization is None:
        raise HTTPException(status_code=401, detail="Authorization: Bearer <Ta> is required")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Authorization: Bearer <Ta> is required")
    return token.strip()


def _sidecar_http_error(exc: SidecarError) -> HTTPException:
    if exc.status_code in (400, 401, 403):
        return HTTPException(status_code=exc.status_code, detail=exc.detail)
    if exc.status_code == 503:
        return HTTPException(status_code=503, detail=exc.detail)
    logger.warning("sidecar exchange failed status=%s", exc.status_code)
    return HTTPException(status_code=502, detail=exc.detail)
