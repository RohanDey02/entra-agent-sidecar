"""HTTP API for the blueprint / agent / gateway token exchange.

Tc is the inbound user token and its audience is the blueprint app id.
The sidecar turns that into T1 for the agent app id, then into Tr for the
gateway. This process never sees T1.
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
from app.tokens import TokenShapeError, require_gateway_token, safe_claims

logger = logging.getLogger(__name__)

_FLOW = {
    "tc": "user token presented to this app; audience is the blueprint app id",
    "t1": "token the sidecar acquires for the agent app id; not returned here",
    "tr": "on-behalf-of token from Tc + T1, requested as the blueprint, for the gateway",
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
        tc = _bearer_token(request.headers.get("authorization"))
        broker: SidecarClient = request.app.state.sidecar
        upstream: GatewayClient = request.app.state.gateway
        current: Settings = request.app.state.settings
        try:
            await asyncio.to_thread(broker.validate_tc, tc)
            tr_header = await asyncio.to_thread(broker.exchange_tc_for_tr, tc)
            claims = safe_claims(tr_header)
            require_gateway_token(
                claims,
                agent_client_id=current.agent_client_id,
                gateway_audience=current.gateway_audience,
            )
            gateway_response = await asyncio.to_thread(upstream.call, tr_header)
        except SidecarError as exc:
            raise _sidecar_http_error(exc) from exc
        except TokenShapeError as exc:
            raise HTTPException(
                status_code=502,
                detail={"message": exc.detail, "trClaims": exc.claims},
            ) from exc
        except GatewayError as exc:
            raise HTTPException(status_code=502, detail=exc.detail) from exc
        return {"flow": _FLOW, "trClaims": claims, "gateway": gateway_response}

    return app


def _bearer_token(authorization: str | None) -> str:
    if authorization is None:
        raise HTTPException(status_code=401, detail="Authorization: Bearer <Tc> is required")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Authorization: Bearer <Tc> is required")
    return token.strip()


def _sidecar_http_error(exc: SidecarError) -> HTTPException:
    if exc.status_code in (400, 401, 403):
        return HTTPException(status_code=exc.status_code, detail=exc.detail)
    if exc.status_code == 503:
        return HTTPException(status_code=503, detail=exc.detail)
    logger.warning("sidecar exchange failed status=%s", exc.status_code)
    return HTTPException(status_code=502, detail=exc.detail)
