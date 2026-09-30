# Entra agent sidecar

Python agent and the [Microsoft Entra ID Auth SDK sidecar](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/overview) in one pod. The app receives a user token and calls a gateway. The sidecar is the only process that talks to Microsoft Entra ID.

## Tokens

| Token | Audience / client | Who handles it |
| --- | --- | --- |
| Tc | Blueprint app ID | The caller sends it to this app. The sidecar validates it. |
| T1 | Agent app ID | The sidecar acquires it with `AgentIdentity`. This app never sees it. |
| Tr | Gateway app ID. `azp` is the agent app ID | The sidecar exchanges Tc + T1, with the blueprint app ID as the OBO client. This app sends Tr only to the gateway. |

The sidecar call is `GET /AuthorizationHeader/Gateway?AgentIdentity=<agent-app-id>` with `Authorization: Bearer <Tc>`. That is the authenticated endpoint from the [sidecar API](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/endpoints). `RequestAppToken` is not set, so the exchange stays on behalf of the user.

Three app registrations are involved:

- **Blueprint.** Sidecar `AzureAd__ClientId` and `AzureAd__Audience`. Holds the credential. Tc is issued for this app ID.
- **Agent identity.** Passed as `AgentIdentity`. T1 is for this app ID, and Tr.`azp` must match it.
- **Gateway.** Its own app ID. The sidecar requests a delegated scope on that API, and Tr.`aud` must match `GATEWAY_AUDIENCE`.

## Pod

On GKE the [installation guide](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/installation) layout is two containers in one pod. They share a network namespace, so the app uses `http://127.0.0.1:5000`.

The sidecar listens on `127.0.0.1:5000` only. The Service publishes port 8080 and has no port 5000. A NetworkPolicy allows ingress only to 8080. Kubelet HTTP probes cannot reach a process bound to `127.0.0.1`, so the app `/ready` probe checks the sidecar `/healthz` over localhost instead of putting an `httpGet` probe on port 5000.

GKE is not AKS. Do not use `SignedAssertionFromManagedIdentity` or the Azure Workload Identity webhook. Those depend on Azure IMDS or the AKS mutating webhook. The chart uses `SignedAssertionFilePath` and mounts a projected service account token on the sidecar container only, which is the [non-Azure Kubernetes credential](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/configuration). The federated credential audience is `api://AzureADTokenExchange`.

This is not GKE Workload Identity for Google APIs. Nothing in the pod impersonates a Google service account. Entra trusts the cluster OIDC issuer directly.

## Local run

Copy `.env.example` to `.env` and fill it in. `AZURE_AD_AUDIENCE` is the blueprint app ID. `BLUEPRINT_CLIENT_SECRET` is for this Compose file only.

```bash
docker compose up --build
curl -s http://127.0.0.1:8080/ready
curl -s http://127.0.0.1:8080/gateway -H "Authorization: Bearer <Tc>"
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
