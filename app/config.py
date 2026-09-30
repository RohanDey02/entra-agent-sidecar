"""Runtime settings. This process never receives a blueprint credential."""

import os
from dataclasses import dataclass
from urllib.parse import urlparse

_SIDECAR_HOSTS = frozenset({"127.0.0.1", "localhost", "sidecar", "::1"})


class ConfigurationError(RuntimeError):
    """Raised when the process is not safe to start."""


@dataclass(frozen=True)
class Settings:
    sidecar_url: str
    agent_client_id: str
    gateway_base_url: str
    gateway_audience: str
    gateway_path: str
    downstream_service: str = "Gateway"

    @classmethod
    def from_env(cls) -> "Settings":
        agent_client_id = os.environ.get("AGENT_CLIENT_ID", "").strip()
        gateway_audience = os.environ.get("GATEWAY_AUDIENCE", "").strip()
        if not agent_client_id:
            raise ConfigurationError("AGENT_CLIENT_ID is required")
        if not gateway_audience or any(char.isspace() for char in gateway_audience):
            raise ConfigurationError("GATEWAY_AUDIENCE is required")
        return cls(
            sidecar_url=normalize_sidecar_url(
                os.environ.get("SIDECAR_URL", "http://127.0.0.1:5000")
            ),
            agent_client_id=agent_client_id,
            gateway_base_url=normalize_gateway_base_url(os.environ.get("GATEWAY_BASE_URL", "")),
            gateway_audience=gateway_audience,
            gateway_path=normalize_gateway_path(os.environ.get("GATEWAY_PATH", "/")),
        )


def normalize_sidecar_url(raw: str) -> str:
    """Allow only the pod-local sidecar or the Compose service name.

    Tc is forwarded to this URL. A public sidecar URL would send the user
    token outside the pod trust boundary.
    """
    parsed = urlparse(raw.strip())
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "http"
        or parsed.username
        or parsed.password
        or host not in _SIDECAR_HOSTS
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError(
            "SIDECAR_URL must be http://127.0.0.1:5000 in Kubernetes "
            "or http://sidecar:5000 in Compose"
        )
    return raw.strip().rstrip("/")


def normalize_gateway_base_url(raw: str) -> str:
    parsed = urlparse(raw.strip())
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not host
        or host in _SIDECAR_HOSTS
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError("GATEWAY_BASE_URL must be an https URL for the gateway")
    return raw.strip().rstrip("/")


def normalize_gateway_path(raw: str) -> str:
    path = raw.strip() or "/"
    if not path.startswith("/") or path.startswith("//") or "\\" in path or ".." in path.split("/"):
        raise ConfigurationError("GATEWAY_PATH must be one relative path on the gateway")
    return path
