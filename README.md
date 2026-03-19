# GitOpsAPI

A FastAPI backend that treats Git as an object store for platform infrastructure. All mutations create a feature branch and raise a pull request — nothing is committed directly to `main`. Clusters, applications, and promotion pipelines are first-class API objects backed by Helm values files in GitOps repositories.

## What it does

| Concern | How |
| --- | --- |
| **Cluster provisioning** | `POST /api/v1/clusters` writes a cluster-chart values file + Flux Kustomization entry, raises a PR to the management GitOps repo. CAPI picks it up and provisions the cluster on Proxmox. |
| **Application management** | `POST /api/v1/applications` registers an app definition. `POST /api/v1/application-configs` assigns it to a cluster, writing a HelmRelease to `{cluster}-apps`. |
| **Promotion pipelines** | `POST /api/v1/pipelines` creates dev→ETE→production promotion pipelines with PR-gated approvals at each stage. |
| **Repository lifecycle** | `POST /api/v1/repositories` creates private `{cluster}-infra` and `{cluster}-apps` repos on the GitHub org, registers deploy keys. |
| **PR governance** | `GET/POST /api/v1/prs` — list, inspect, approve, and merge PRs. Stage labels (`stage:dev`, `stage:ete`, `stage:production`) drive required-approver rules. |

## Architecture

```text
Client → FastAPI (gitopsgui) → GitService (HTTPS+PAT) → {cluster}-infra / {cluster}-apps repos
                             → GitHubService (PyGitHub) → PR lifecycle
                             → K8sService              → cluster status reads
```

Each cluster has **two repos** on the GitHub org:

- `{cluster}-infra` — Flux Kustomization entries (`clusters/{cluster}/{cluster}-apps.yaml`)
- `{cluster}-apps` — HelmRelease + HelmRepository manifests (`gitops/gitops-apps/{name}/{name}.yaml`)

Routing between repos is handled by `repo_router.py`. The management repo hosts cluster-chart values (`gitops/cluster-charts/{name}/{name}-values.yaml`).

## API surface

| Endpoint group | Routes |
| --- | --- |
| `/api/v1/clusters` | CRUD + suspend + decommission |
| `/api/v1/applications` | CRUD |
| `/api/v1/application-configs` | CRUD — assigns an application to a cluster |
| `/api/v1/pipelines` | Create + list |
| `/api/v1/prs` | List + get + approve + merge |
| `/api/v1/repositories` | Create repo + deploy key management |
| `/api/v1/status` | Health + readiness |

Full OpenAPI schema available at `/docs` when running.

## Key models

**`ClusterSpec`** — the central object. Drives cluster-chart Helm values and CAPI `ProxmoxCluster` manifests.

```python
ClusterSpec(
    name="platform-services",
    platform=PlatformSpec(
        name="venus",
        endpoint="https://192.168.4.50:8006",
        nodes=["venus"],
        template_vmid=100,
        credentials_ref="capmox-manager-credentials",
    ),
    vip="192.168.4.180",
    ip_range="192.168.4.181-192.168.4.187",
    dimensions=ClusterDimensions(control_plane_count=1, worker_count=2),
    sops_secret_ref="gitopsapi-age-key",
    external_hosts=["login.podzone.cloud"],
)
```

`PlatformSpec` maps directly to cluster-chart `proxmox:` values — `nodes` → `allowedNodes`, `template_node` → `sourcenode`, etc.

## Local development

```bash
# Install
uv venv && source .venv/bin/activate && uv pip install -e ".[dev]"

# Minimal .env.local
export GITOPS_SKIP_INIT=1
export GITOPS_SKIP_PUSH=1
export GITOPS_SKIP_GITHUB=1
export GITOPSGUI_DEV_ROLE=cluster_operator

# Run
uvicorn src.gitopsgui.main:app --reload --port 8000

# Test
.venv/bin/pytest -q --tb=short     # 228 tests
```

See [docs/claude-code-setup-guide.md](docs/claude-code-setup-guide.md) for the full development reference.

## Deployment

Helm chart published to GitHub Pages: `https://motttt.github.io/gitopsapi`

```yaml
# HelmRepository
url: https://motttt.github.io/gitopsapi

# HelmRelease
chart: gitopsapi
version: "0.1.3"   # appVersion: v0.1.6
```

Required Kubernetes secrets: `gitopsapi-github-token`, `gitopsapi-mgmt-kubeconfig`, `gitopsapi-age-key` (key name: `key.txt`).

See [docs/deployment-prerequisites.md](docs/deployment-prerequisites.md) for full prerequisites.

## Auth

OAuth2 proxy (Keycloak) injects `X-Auth-Request-Groups` headers. Groups map to roles in `src/gitopsgui/api/auth.py`. Endpoints declare required roles via `require_role("cluster_operator")`.

Dev bypass: `GITOPSGUI_DEV_ROLE=cluster_operator` skips all auth checks.

## Documentation

| Document | Purpose |
| --- | --- |
| [docs/claude-code-setup-guide.md](docs/claude-code-setup-guide.md) | Full developer reference — models, patterns, gotchas |
| [docs/architecture/v0.1.0-architecture.md](docs/architecture/v0.1.0-architecture.md) | System architecture |
| [docs/api-first-testing-protocol.md](docs/api-first-testing-protocol.md) | Testing protocol — attribute review before API calls |
| [docs/application-catalog.md](docs/application-catalog.md) | Managed application catalog |
| [docs/deployment-prerequisites.md](docs/deployment-prerequisites.md) | Deployment prerequisites |
