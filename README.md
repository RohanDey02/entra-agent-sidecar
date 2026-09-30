# Entra agent sidecar

Python agent and the [Microsoft Entra ID Auth SDK sidecar](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/overview) in one GKE pod. The UI login token stops at the ACP Gateway. This app receives Ta and calls the resource with Tr. The sidecar is the only process that talks to Microsoft Entra ID.

## Handshake

```text
Browser                ACP Gateway              agent :8080              sidecar 127.0.0.1:5000         Entra
  |  Tc (UI login)          |                        |                            |                        |
  |------------------------>|                        |                            |                        |
  |                         |  Ta                    |                            |                        |
  |                         |  aud = blueprint       |                            |                        |
  |                         |  azp = ACP Gateway     |                            |                        |
  |                         |----------------------->|                            |                        |
  |                         |                        |  GET /Validate             |                        |
  |                         |                        |  Authorization: Bearer Ta  |                        |
  |                         |                        |--------------------------->|                        |
  |                         |                        |  claims                    |                        |
  |                         |                        |<---------------------------|                        |
  |                         |                        |                            |  projected SA token    |
  |                         |                        |                            |  aud = api://AzureADTokenExchange
  |                         |                        |                            |----------------------->|
  |                         |                        |                            |  T1                    |
  |                         |                        |                            |  aud = api://AzureADTokenExchange
  |                         |                        |                            |<-----------------------|
  |                         |                        |  GET /AuthorizationHeader/Gateway?AgentIdentity=<agent>
  |                         |                        |  Authorization: Bearer Ta  |                        |
  |                         |                        |--------------------------->|                        |
  |                         |                        |                            |  OBO: assertion=Ta     |
  |                         |                        |                            |       client_assertion=T1
  |                         |                        |                            |----------------------->|
  |                         |                        |                            |  Tr                    |
  |                         |                        |                            |  azp = agent app id    |
  |                         |                        |  Authorization: Bearer Tr  |                        |
  |                         |                        |<---------------------------|                        |
  |                         |                        |  Tr                        |                        |
  |                         |                        |-------------------------------- resource --------------->|
```

Tc never enters the pod. T1 never enters the agent container. The projected service-account token is the credential the sidecar uses to obtain T1. It is mounted only on the sidecar, at `/var/run/secrets/entra/token`, with audience `api://AzureADTokenExchange`.

This matches the [agent on-behalf-of flow](https://learn.microsoft.com/en-us/entra/agent-id/agent-on-behalf-of-oauth-flow). T1 is requested with scope `api://AzureADTokenExchange/.default` and `fmi_path` set to the agent app ID. The OBO assertion is Ta, not Tc.

### What the agent sends

Both calls go to `SIDECAR_URL`. On GKE that is `http://127.0.0.1:5000`, because the two containers share the pod network. In Compose it is `http://sidecar:5000`, because Compose gives each service its own network namespace.

Validate Ta:

```http
GET /Validate HTTP/1.1
Host: 127.0.0.1:5000
Authorization: Bearer <Ta>
```

The sidecar checks the signature, issuer, audience, and expiry. The agent then requires:

- `aud` equals `BLUEPRINT_APP_ID`
- `azp` equals `ACP_GATEWAY_APP_ID`

A Ta that fails those checks is rejected with HTTP 403. The exchange does not run.

Exchange Ta + T1 for Tr:

```http
GET /AuthorizationHeader/Gateway?AgentIdentity=<AGENT_CLIENT_ID> HTTP/1.1
Host: 127.0.0.1:5000
Authorization: Bearer <Ta>
```

`AgentIdentity` is the agent app ID. It is the `fmi_path` for T1. The response is:

```json
{ "authorizationHeader": "Bearer <Tr>" }
```

The agent sends Tr to the resource only when:

- `azp` equals `AGENT_CLIENT_ID`
- `aud` equals `GATEWAY_AUDIENCE`

`GET /gateway` on the agent is the caller-facing endpoint. It expects `Authorization: Bearer <Ta>` and returns the safe claims plus the resource response. It does not return Tc, T1, or the raw Tr.

### Who holds which secret

| Process | Sees | Does not see |
| --- | --- | --- |
| ACP Gateway | Tc, and the Ta it mints | T1, the projected token |
| Agent container | Ta, Tr | T1, blueprint credential, projected token |
| Sidecar container | Ta, T1, Tr, projected token | The UI session |

The sidecar `AzureAd__ClientId` is the blueprint app ID. That application holds the federated credential. `RequestAppToken` is not set, so Tr stays an on-behalf-of token.

## Tokens

| Token | Claims | Who handles it |
| --- | --- | --- |
| Tc | UI login token | The browser sends it to the ACP Gateway. This app never sees it. |
| Ta | `aud` = blueprint app ID, `azp` = ACP Gateway app ID | The ACP Gateway mints it from Tc. This app forwards it to the sidecar. |
| T1 | `aud` = `api://AzureADTokenExchange` | The sidecar acquires it for the agent app ID. This app never sees it. |
| Tr | `azp` = agent app ID, `aud` = resource | The sidecar exchanges Ta + T1. This app sends Tr only to the resource. |

- **Blueprint.** Sidecar `AzureAd__ClientId` and `AzureAd__Audience`. Holds the federated credential. Ta is issued for this app ID.
- **ACP Gateway.** Mints Ta. Ta.`azp` must match `ACP_GATEWAY_APP_ID`.
- **Agent identity.** Passed as `AgentIdentity`. Tr.`azp` must match it.
- **Resource.** Tr.`aud` must match `GATEWAY_AUDIENCE`.

## Environment variables

The agent and the sidecar read different variables. The same blueprint app ID is passed to both, for different checks. The blueprint client secret is a Compose input only. On GKE the sidecar reads the projected token instead.

`.env` is read by Compose. The chart reads `deploy/overlays/gke/values.yaml` into ConfigMap `entra-agent`. Inspect that ConfigMap with:

```bash
kubectl -n entra-agent get configmap entra-agent -o yaml
kubectl -n entra-agent set env deploy/entra-agent --list -c agent
kubectl -n entra-agent set env deploy/entra-agent --list -c sidecar
```

### Agent container

The process exits on startup when a required variable is missing. `SIDECAR_URL` must be `http://127.0.0.1:5000`, `http://localhost:5000`, or `http://sidecar:5000`. `GATEWAY_BASE_URL` must be `https`.

| Variable | Required | GKE source | Purpose |
| --- | --- | --- | --- |
| `SIDECAR_URL` | No. Defaults to `http://127.0.0.1:5000` | Chart value `agent.sidecarUrl` | Where Ta is sent. Compose sets `http://sidecar:5000`. |
| `BLUEPRINT_APP_ID` | Yes | `blueprintClientId` | Expected `aud` of Ta. |
| `ACP_GATEWAY_APP_ID` | Yes | `acpGatewayAppId` | Expected `azp` of Ta. |
| `AGENT_CLIENT_ID` | Yes | `agentClientId` | `AgentIdentity` query parameter. Expected `azp` of Tr. |
| `GATEWAY_BASE_URL` | Yes | `gatewayBaseUrl` | Resource the agent calls with Tr. |
| `GATEWAY_AUDIENCE` | Yes | `gatewayAudience` | Expected `aud` of Tr. |
| `GATEWAY_PATH` | No. Defaults to `/` | `gatewayPath` | One relative path on that resource. |

### Sidecar container

These are ASP.NET configuration keys. On GKE they come from the same ConfigMap, except the credential type and the listen address, which the chart sets directly.

| Variable | GKE value | Purpose |
| --- | --- | --- |
| `AzureAd__Instance` | `https://login.microsoftonline.com/` | Entra authority. |
| `AzureAd__TenantId` | `tenantId` | Tenant that issues T1 and Tr. |
| `AzureAd__ClientId` | `blueprintClientId` | Blueprint app ID. Owns the federated credential used to request T1. |
| `AzureAd__Audience` | `inboundAudience` | Audience accepted on Ta. Set this to the blueprint app ID. |
| `AzureAd__Scopes` | `inboundScope`, default `access_as_user` | Scope required on Ta before the exchange. |
| `AzureAd__ClientCredentials__0__SourceType` | `SignedAssertionFilePath` | How the sidecar authenticates to Entra. |
| `AzureAd__ClientCredentials__0__SignedAssertionFileDiskPath` | `/var/run/secrets/entra/token` | Projected token whose audience is `api://AzureADTokenExchange`. |
| `DownstreamApis__Gateway__BaseUrl` | `gatewayBaseUrl` | Names the `Gateway` API used by `/AuthorizationHeader/Gateway`. |
| `DownstreamApis__Gateway__Scopes__0` | `gatewayScope` | Delegated scope requested for Tr, such as `api://<GATEWAY_APP_ID>/access_as_user`. |
| `Kestrel__Endpoints__Http__Url` | `http://127.0.0.1:5000` | Sidecar listen address. Pod-local only. |
| `ASPNETCORE_ENVIRONMENT` | `Production` | Keeps the sidecar OpenAPI endpoint off. |

`gatewayScope` is the scope of the resource Tr is for. It is a delegated scope. `api://AzureADTokenExchange/.default` is the scope of T1, and the sidecar requests that itself during the exchange. Do not put that value in `DownstreamApis__Gateway__Scopes__0`.

### Compose-only inputs

`.env.example` maps these into the containers above. Two of them exist only for local Compose:

| `.env` name | Container variable | Notes |
| --- | --- | --- |
| `TENANT_ID` | `AzureAd__TenantId` | |
| `BLUEPRINT_APP_ID` | Agent `BLUEPRINT_APP_ID` and sidecar `AzureAd__ClientId` | |
| `AZURE_AD_AUDIENCE` | `AzureAd__Audience` | Blueprint app ID, same value as `BLUEPRINT_APP_ID` when Ta.`aud` is that app ID. |
| `ACP_GATEWAY_APP_ID` | Agent `ACP_GATEWAY_APP_ID` | |
| `AGENT_CLIENT_ID` | Agent `AGENT_CLIENT_ID` | |
| `GATEWAY_BASE_URL` | Both containers | |
| `GATEWAY_SCOPE` | `DownstreamApis__Gateway__Scopes__0` | Sidecar only. |
| `GATEWAY_AUDIENCE` | Agent `GATEWAY_AUDIENCE` | |
| `GATEWAY_PATH` | Agent `GATEWAY_PATH` | Defaults to `/`. |
| `INBOUND_SCOPE` | `AzureAd__Scopes` | Defaults to `access_as_user`. |
| `BLUEPRINT_CLIENT_SECRET` | `AzureAd__ClientCredentials__0__ClientSecret` | Compose only. `SourceType` is `ClientSecret` there. |

Compose also sets `ASPNETCORE_URLS=http://+:5000` so the agent container can reach the sidecar by service name. That listen address is not used on GKE.

## What kubectl shows

The [installation guide](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/installation) layout is two containers in one pod. Render and apply the GKE overlay after replacing the placeholders in `deploy/overlays/gke/values.yaml`:

```bash
kubectl kustomize --enable-helm deploy/overlays/gke | kubectl apply -f -
kubectl -n entra-agent rollout status deploy/entra-agent
```

A healthy pod reports two ready containers. The names are `agent` and `sidecar`.

```text
NAMESPACE      NAME                           READY   STATUS    RESTARTS
entra-agent    entra-agent-7d9f8b6c4d-xk2pl   2/2     Running   0
```

`READY 2/2` means the `agent` readiness probe passed. That probe calls the agent `/ready`, which calls the sidecar `GET /healthz` on `127.0.0.1:5000`. The sidecar has no kubelet probe of its own: it listens only on loopback, and kubelet probes arrive on the pod IP. If the sidecar process is up but `/healthz` fails, the pod stays `1/2`.

```bash
kubectl -n entra-agent get pod,svc,sa,cm,netpol
kubectl -n entra-agent get pod -l app.kubernetes.io/name=entra-agent \
  -o jsonpath='{range .items[*].spec.containers[*]}{.name}{"\t"}{.image}{"\n"}{end}'
```

The second command prints two lines: `agent` and `sidecar`. The Service is `entra-agent`, type `ClusterIP`, port `80` targeting container port `8080`. It has no port `5000`.

```bash
kubectl -n entra-agent get svc entra-agent -o yaml
```

`kubectl describe pod` in that namespace shows the split:

- Both containers use service account `entra-agent`. `automountServiceAccountToken` is false.
- Only `sidecar` mounts `entra-token` at `/var/run/secrets/entra`. The projected token audience is `api://AzureADTokenExchange`.
- `sidecar` env `AzureAd__ClientCredentials__0__SourceType` is `SignedAssertionFilePath`.
- `sidecar` env `Kestrel__Endpoints__Http__Url` is `http://127.0.0.1:5000`.
- `agent` env `SIDECAR_URL` is `http://127.0.0.1:5000`.

GKE is not AKS. Do not use `SignedAssertionFromManagedIdentity` or the Azure Workload Identity webhook. The chart uses the [non-Azure Kubernetes credential](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/configuration). Nothing in the pod impersonates a Google service account. Entra trusts the cluster OIDC issuer directly.

Before apply, add a federated identity credential on the blueprint application:

- Issuer: the `issuer` from `kubectl get --raw /.well-known/openid-configuration`. Entra must be able to fetch that URL and its JWKS from the internet.
- Subject: `system:serviceaccount:entra-agent:entra-agent`
- Audience: `api://AzureADTokenExchange`

### Logs and a loopback check

Sidecar logs are the Entra exchange. Agent logs are the handshake result. Neither container should be asked to print a token.

```bash
kubectl -n entra-agent logs deploy/entra-agent -c sidecar --tail=100
kubectl -n entra-agent logs deploy/entra-agent -c agent --tail=100
```

The sidecar image does not include a shell. Check `/healthz` from the agent container, which shares the network namespace:

```bash
kubectl -n entra-agent exec deploy/entra-agent -c agent -- \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:5000/healthz').status)"
```

`200` means the agent can reach the sidecar. To call the handshake yourself, forward only the Service port:

```bash
kubectl -n entra-agent port-forward svc/entra-agent 8080:80
curl -s http://127.0.0.1:8080/ready
curl -s http://127.0.0.1:8080/gateway -H "Authorization: Bearer <Ta>"
```

Do not port-forward port `5000`. That would publish the token endpoint outside the pod.

`GATEWAY_AUDIENCE` has to be the resource token's `aud`. Use the resource app ID when that is the audience, or `api://<GATEWAY_APP_ID>` when the application ID URI is the audience. `gatewayScope` is the delegated scope requested for that API, not `/.default`.

## Local run

Copy `.env.example` to `.env` and fill it in. `AZURE_AD_AUDIENCE` is the blueprint app ID, which is Ta's audience. `BLUEPRINT_CLIENT_SECRET` is for this Compose file only.

```bash
docker compose up --build
curl -s http://127.0.0.1:8080/ready
curl -s http://127.0.0.1:8080/gateway -H "Authorization: Bearer <Ta>"
```

Compose listens on `http://+:5000` inside the sidecar container and does not publish that port on the host. The agent uses `http://sidecar:5000`.

## Tests

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```
