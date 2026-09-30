# Entra agent sidecar

Python agent and the [Microsoft Entra ID Auth SDK sidecar](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/overview) in one pod. The UI login token stops at the ACP Gateway. This app receives Ta and calls the resource with Tr. The sidecar is the only process that talks to Microsoft Entra ID.

## Tokens

| Token | Claims | Who handles it |
| --- | --- | --- |
| Tc | UI login token | The browser sends it to the ACP Gateway. This app never sees it. |
| Ta | `aud` = blueprint app ID, `azp` = ACP Gateway app ID | The ACP Gateway mints it from Tc. This app forwards it to the sidecar. |
| T1 | `aud` = `api://AzureADTokenExchange` | The sidecar acquires it for the agent app ID (`fmi_path`). This app never sees it. |
| Tr | `azp` = agent app ID, `aud` = resource | The sidecar exchanges Ta + T1. This app sends Tr only to the resource. |

The exchange follows the [agent on-behalf-of flow](https://learn.microsoft.com/en-us/entra/agent-id/agent-on-behalf-of-oauth-flow). T1 is requested with scope `api://AzureADTokenExchange/.default`. The OBO assertion is Ta, not Tc. The sidecar call is `GET /AuthorizationHeader/Gateway?AgentIdentity=<agent-app-id>` with `Authorization: Bearer <Ta>`.

- **Blueprint.** Sidecar `AzureAd__ClientId` and `AzureAd__Audience`. Holds the federated credential. Ta is issued for this app ID.
- **ACP Gateway.** Mints Ta. Ta.`azp` must match `ACP_GATEWAY_APP_ID`.
- **Agent identity.** Passed as `AgentIdentity`. Tr.`azp` must match it.
- **Resource.** Tr.`aud` must match `GATEWAY_AUDIENCE`.

## Pod

On GKE the [installation guide](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/installation) layout is two containers in one pod. They share a network namespace, so the app uses `http://127.0.0.1:5000`.

The sidecar listens on `127.0.0.1:5000` only. The Service publishes port 8080 and has no port 5000. A NetworkPolicy allows ingress only to 8080. Kubelet HTTP probes cannot reach a process bound to `127.0.0.1`, so the app `/ready` probe checks the sidecar `/healthz` over localhost instead of putting an `httpGet` probe on port 5000.

GKE is not AKS. Do not use `SignedAssertionFromManagedIdentity` or the Azure Workload Identity webhook. Those depend on Azure IMDS or the AKS mutating webhook. The chart uses `SignedAssertionFilePath` and mounts a projected service account token on the sidecar container only, which is the [non-Azure Kubernetes credential](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/configuration). The federated credential audience is `api://AzureADTokenExchange`.

This is not GKE Workload Identity for Google APIs. Nothing in the pod impersonates a Google service account. Entra trusts the cluster OIDC issuer directly.

## Local run

Copy `.env.example` to `.env` and fill it in. `AZURE_AD_AUDIENCE` is the blueprint app ID, which is Ta's audience. `BLUEPRINT_CLIENT_SECRET` is for this Compose file only.

```bash
docker compose up --build
curl -s http://127.0.0.1:8080/ready
curl -s http://127.0.0.1:8080/gateway -H "Authorization: Bearer <Ta>"
```

Compose gives the sidecar its own network namespace, so that file listens on `http://+:5000` and the app uses `http://sidecar:5000`. The sidecar port is not published on the host.

## GKE

The chart is `chart/entra-agent`. The GKE overlay renders it:

```bash
kubectl kustomize --enable-helm deploy/overlays/gke
```

Replace the placeholders in `deploy/overlays/gke/values.yaml`, push the app image to Artifact Registry, and set `agent.image.repository`. Then apply the rendered manifests.

Before apply, add a federated identity credential on the **blueprint** application:

- Issuer: the `issuer` from `kubectl get --raw /.well-known/openid-configuration`. Entra must be able to fetch that URL and its JWKS from the internet.
- Subject: `system:serviceaccount:entra-agent:entra-agent`
- Audience: `api://AzureADTokenExchange`

The projected token is mounted only on the sidecar, at `/var/run/secrets/entra/token`.

`GATEWAY_AUDIENCE` has to be the gateway access token's `aud`. Use the gateway app ID when that is the audience, or `api://<GATEWAY_APP_ID>` when the gateway's application ID URI is the audience. `gatewayScope` is the delegated scope requested for that API, not `/.default`.

## Tests

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```
