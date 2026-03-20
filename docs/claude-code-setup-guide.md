# Claude Code Project Setup Guide — gitopsapi

Audience: a new Claude Code session. Dense and practical. Read top-to-bottom once, then jump by section name.

---

## 1. What this project is

**gitopsapi** is a FastAPI backend that treats git as an object store for infrastructure state. All write operations (cluster provisioning, application deployment, configuration changes) produce a feature-branch + GitHub PR rather than applying changes directly. Flux on the management cluster reconciles merged PRs.

Key concepts:

- **CAPI cluster provisioning**: `POST /api/v1/clusters` writes a cluster-chart HelmRelease + values ConfigMap + Kustomization wiring to git. On PR merge, Flux creates a CAPI `Cluster` object via the cluster-chart Helm chart.
- **Helm application management**: `POST /api/v1/applications` writes a HelmRelease + HelmRepository to the cluster's `-apps` repo.
- **Application-cluster configuration**: `POST /api/v1/application-configs` links an application to a cluster, writing a Kustomization entry to the cluster's `-infra` repo and a per-cluster values override to `-apps`.
- **PR governance**: the `/api/v1/prs` endpoints list, approve, and merge PRs with role-gated approval rules.
- **No direct k8s writes** (except kubeconfig retrieval): all provisioning goes through git.

Source of truth for requirements: `podzoneAgentTeam/specifications/gitopsgui-requirements.md` (v0.4). API schema: `podzoneAgentTeam/specifications/gitopsapi-schema.md`.

---

## 2. Repository layout

```
gitopsapi/
├── src/gitopsgui/
│   ├── api/
│   │   ├── main.py              — FastAPI app, lifespan (git init), router mounts
│   │   ├── auth.py              — require_role(), _RoleChecker, _extract_caller
│   │   └── routers/             — One file per resource: clusters, applications,
│   │                              application_configs, pipelines, prs, repositories, status
│   ├── services/
│   │   ├── git_service.py       — GitService: clone/pull/branch/write/commit/push
│   │   ├── github_service.py    — GitHubService: PRs, repo create/archive, deploy keys
│   │   ├── repo_router.py       — Per-cluster GitService/GitHubService factory
│   │   ├── cluster_service.py   — ClusterService: CRUD + suspend/decommission
│   │   ├── app_service.py       — AppService: create/list/get/disable/enable
│   │   ├── app_config_service.py — ApplicationClusterConfig: create/list/get/patch/delete
│   │   ├── pipeline_service.py  — Promotion pipeline support
│   │   ├── deploy_key_service.py — Deploy key generation (CC-053, partial)
│   │   ├── sops_service.py      — SOPS age key bootstrap (CC-053)
│   │   ├── k8s_service.py       — Kubernetes client (kubeconfig retrieval)
│   │   └── kubeconfig_service.py — Kubeconfig fetch + bastion rewrite
│   ├── models/
│   │   ├── cluster.py           — ClusterSpec, ClusterDimensions, PlatformSpec, ClusterResponse
│   │   ├── application.py       — ApplicationSpec, ApplicationResponse
│   │   ├── application_config.py — ApplicationClusterConfig, PatchApplicationClusterConfig
│   │   ├── pipeline.py          — PipelineSpec
│   │   └── pr.py                — PRDetail, ReviewerStatus
│   └── mcp/                     — MCP context server (Qdrant integration)
├── charts/gitopsapi/            — Helm chart for deploying gitopsapi itself
│   ├── Chart.yaml               — chart version 0.1.3, appVersion v0.1.6
│   ├── values.yaml              — image, gitops, secrets, expose, resources
│   └── templates/               — Deployment, Service, ConfigMap, HTTPRoute, etc.
├── tests/
│   ├── conftest.py              — TestClient fixture, headers_for(), sample payloads
│   ├── test_auth.py             — Auth unit tests
│   ├── test_routers/            — Router tests (role enforcement, response shape)
│   ├── test_services/           — Service unit tests (GitService/GitHubService mocked)
│   ├── test_models/             — Pydantic model tests
│   └── test_data/               — JSON fixture files for clusters, applications, app-configs
├── docs/
│   ├── deploy/                  — HelmRelease manifests for deploying gitopsapi to clusters
│   ├── architecture/            — v0.1.0 architecture notes
│   └── application-catalog.md  — Registry of all managed applications
├── CLAUDE.md                    — Session context (YOU ARE HERE)
├── QUESTIONS.md                 — Blockers and questions for Team Lead
├── pyproject.toml               — Python 3.11+, setuptools, pytest config
├── Dockerfile                   — Multi-stage: API only (Dockerfile.api-only on erectus)
└── docker-compose.yml           — Local dev stack
```

---

## 3. Local dev setup

### Prerequisites

Python 3.11+. The project uses a `.venv` in the repo root managed by `uv` (or plain `pip`).

```bash
cd /Users/martincolley/workspace/gitopsapi
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Or if `uv` is available (preferred, matches `uv.lock`):

```bash
uv sync
```

### Environment variables

Create `.env.local` (already gitignored) for local dev. Minimal working set:

```bash
GITOPS_LOCAL_PATH=/Users/martincolley/workspace/cluster-charts  # local clone of management repo
GITOPS_BRANCH=main
GITOPS_SKIP_INIT=1       # skip git clone on startup (repo already exists locally)
GITOPS_SKIP_PUSH=1       # skip git push (writes stay local)
GITOPS_SKIP_GITHUB=1     # use LocalPRStore (file-backed) instead of GitHub API
GITOPSGUI_DEV_ROLE=cluster_operator  # bypass OAuth2 proxy auth
GITHUB_REPO=your-org/cluster-charts    # used by GitHubService default constructor
GITHUB_ORG=your-org                    # org for per-cluster repo URL generation
```

Additional variables used in production / E2E:

```bash
GITOPS_REPO_URL=https://github.com/your-org/cluster-charts.git  # management repo HTTPS URL
GITHUB_TOKEN=<PAT>                 # injected into HTTPS URLs for auth; also used by PyGitHub
GITHUB_REPO=your-org/cluster-charts      # default repo for GitHubService
MGMT_KUBECONFIG_SECRET=<yaml>     # base64-decoded kubeconfig written to /tmp/mgmt-kubeconfig
GITOPS_REPOS_BASE=/tmp/gitops-repos  # base path for per-cluster repo clones
```

### Running the server

```bash
export $(cat .env.local | xargs)
.venv/bin/uvicorn gitopsgui.api.main:app --reload --port 8000
```

API docs at `http://localhost:8000/docs`.

Health probe: `GET /health` (no auth). Readiness: `GET /ready` (503 until lifespan completes).

### Running tests

```bash
.venv/bin/pytest -q --tb=short                                            # all tests
.venv/bin/pytest -q --tb=short tests/test_services/test_app_service.py   # single module
.venv/bin/pytest -q --tb=short tests/test_routers/                       # all router tests
```

Tests do NOT require any env vars to be set — `conftest.py` sets the required defaults via `os.environ.setdefault(...)` before any imports. The `client` fixture patches `GitService.init` so no git clone runs.

Current baseline: 228+ tests passing (as of v0.1.6 / PROJ-003/T-012).

---

## 4. Auth pattern — critical gotcha

Auth flows via OAuth2 proxy headers injected by an in-cluster sidecar:

- `X-Forwarded-User` → username
- `X-Auth-Request-Groups` → comma-separated Keycloak group names

Groups map to roles in `src/gitopsgui/api/auth.py`:

```python
_GROUP_TO_ROLE = {
    "cluster-operators": "cluster_operator",
    "build-managers":    "build_manager",
    "senior-developers": "senior_developer",
    "security-admins":   "security_admin",
}
```

### CRITICAL: require_role usage

`require_role()` returns a `Depends(...)` object. Use it **directly as a parameter default** — do NOT wrap in another `Depends()`:

```python
# CORRECT
@router.post("/clusters")
async def provision_cluster(spec: ClusterSpec, _=require_role("cluster_operator")):
    ...

# CORRECT — capture caller for use in handler
@router.get("/clusters/{name}/kubeconfig")
async def get_kubeconfig(name: str, caller=require_role("cluster_operator", "build_manager")):
    kubeconfig = await svc.get_kubeconfig(name, caller.role)
    ...

# WRONG — double-wraps, FastAPI resolves incorrectly
async def bad_endpoint(spec: ClusterSpec, _=Depends(require_role("cluster_operator"))):
    ...
```

`require_role()` is implemented as a factory that returns `Depends(_RoleChecker(*roles))`. `_RoleChecker` is a callable class whose `__call__` has its own `Depends(_extract_caller)` nested inside. This pattern works correctly with FastAPI's dependency resolver; the closure pattern does not.

### Dev bypass

Set `GITOPSGUI_DEV_ROLE=cluster_operator` to skip OAuth2 header validation. The value must be one of the role strings (not the group name). Tests use this via `conftest.py`.

### Test headers

To test role enforcement in router tests:

```python
from tests.conftest import CLUSTER_OP_HEADERS, BUILD_MGR_HEADERS, SENIOR_DEV_HEADERS, NO_ROLE_HEADERS
# e.g. CLUSTER_OP_HEADERS = {"X-Forwarded-User": "testuser", "X-Auth-Request-Groups": "cluster-operators"}
```

---

## 5. Multi-repo layout per cluster

Each cluster has exactly two git repos:

| Repo | What lives there |
|---|---|
| `{cluster}-infra` | `clusters/{cluster}/{cluster}-apps.yaml` — Flux Kustomization entries (one per app assigned to this cluster) |
| `{cluster}-apps` | `gitops/gitops-apps/{name}/{name}.yaml` — HelmRepository + HelmRelease; `{name}-values.yaml` — default values; `{name}-values-{cluster}.yaml` — per-cluster override |

The management repo (GITOPS_REPO_URL, default `cluster09`) holds cluster-chart HelmRelease files:

```
gitops/cluster-charts/{name}/{name}.yaml         — HelmRelease + HelmRepository
gitops/cluster-charts/{name}/{name}-values.yaml  — cluster-chart values (also GitOpsAPI metadata for roundtrip)
gitops/cluster-charts/{name}/kustomization.yaml
gitops/cluster-charts/{name}/kustomizeconfig.yaml
clusters/ManagementCluster/clusters.yaml         — Kustomization entries for all clusters (suspend/decommission target)
```

### repo_router.py

`src/gitopsgui/services/repo_router.py` provides factory functions that construct per-cluster GitService / GitHubService instances:

```python
git_for_apps(cluster)    # GitService targeting {cluster}-apps repo
git_for_infra(cluster)   # GitService targeting {cluster}-infra repo
github_for_apps(cluster) # GitHubService targeting {owner}/{cluster}-apps
github_for_infra(cluster)# GitHubService targeting {owner}/{cluster}-infra
```

Repo URL convention: `https://github.com/{GITHUB_ORG}/{cluster}-apps.git` (HTTPS, not SSH). The `_owner()` helper in `repo_router.py` derives the org from `GITHUB_ORG` (preferred) or falls back to splitting `GITHUB_REPO`.

### Service injection pattern

Services that operate on the management repo use `self._git = GitService()` / `self._gh = GitHubService()` constructed in `__init__`. Services that need per-cluster routing call the `repo_router` helpers inline. In tests, inject mocks directly:

```python
svc = AppService()
svc._git = AsyncMock()   # bypass real git
svc._gh = AsyncMock()    # bypass GitHub API
```

For services using `repo_router` (AppConfigService, parts of AppService), patch the router functions:

```python
with patch("gitopsgui.services.app_config_service.git_for_infra", return_value=mock_git):
    ...
```

---

## 6. GitService — instance-based, HTTPS+PAT auth

`GitService` is **not a singleton**. Each instance wraps one git repo. The constructor:

```python
GitService(repo_url=None, local_path=None)
```

- `repo_url=None` → reads `GITOPS_REPO_URL` at construction time
- `repo_url=""` → empty string → raises `RuntimeError` on first use (not at construction time — lazy init via `_get_repo()`)
- `local_path=None` → uses `GITOPS_LOCAL_PATH` env var (default `/tmp/gitops-repo`)

### Auth: HTTPS + PAT only (SSH removed)

Auth is HTTPS + `GITHUB_TOKEN` injected into the URL by `_auth_url()`:

```python
# Transforms:  https://github.com/org/repo.git
# Into:        https://<TOKEN>@github.com/org/repo.git
```

SSH support was removed in v0.1.6. Any SSH URL (`git@...`) will fall through to `_ssh_env()` which uses `GITOPS_SSH_KEY_PATH`, but this path is no longer used in production. All `repo_url` values in repo_router output `https://` URLs.

### SKIP flags

```
GITOPS_SKIP_INIT=1   — on startup, if repo already cloned locally, skip pull; open existing Repo object
GITOPS_SKIP_PUSH=1   — skip push() entirely (commits happen locally, no remote push)
GITOPS_SKIP_GITHUB=1 — GitHubService uses LocalPRStore (file at GITOPS_LOCAL_PATH/.local-prs.json)
```

All three should be set for local development. Do not combine `SKIP_INIT=1` with a non-existent local path — the guard checks for `.git/` presence.

### Lazy init

Repos are cloned on first call to any read/write method (`read_file`, `create_branch`, etc.) via `_get_repo()`. The lifespan in `main.py` calls `await git.init()` to eagerly clone the management repo on startup.

### Write workflow

All writes follow this sequence — never commit directly to main:

```python
await git.create_branch(branch_name)    # checkout -b from main (fetches first unless SKIP_INIT)
await git.write_file(path, content)     # write + git add
await git.commit(message)              # returns SHA
await git.push()                        # no-op if SKIP_PUSH
pr_url = await gh.create_pr(branch, title, body, labels, reviewers)
```

---

## 7. GitHubService

`GitHubService(repo_name=None)` — `repo_name` is `"owner/repo"`. Defaults to `GITHUB_REPO` env var.

When `GITOPS_SKIP_GITHUB=1`, all GitHub operations use `LocalPRStore` (a JSON file at `{GITOPS_LOCAL_PATH}/.local-prs.json`). This enables full local E2E testing including PR approval + merge (merge does a real squash merge locally via gitpython).

### `create_repo` — idempotent, org vs user

`create_repo()` tries `gh.get_organization(owner).create_repo(...)` first; falls back to `gh.get_user().create_repo(...)` if the owner is a user account, not an org. Existing private repos are returned without error. Existing public repos raise `RuntimeError` (TR-032 violation).

**Important**: `gh.get_user()` with no arguments returns the authenticated user. `gh.get_user(owner)` looks up a specific user by login. Use `gh.get_user()` (no args) when creating repos under the authenticated account.

### PR labels

Convention: resource type label + stage label:

- Resource: `cluster`, `application`, `pipeline`, `promotion`
- Stage: `stage:dev`, `stage:ete`, `stage:production`

Stage determines required approvers in `_STAGE_REQUIRED_ROLES`:

```python
"dev":        ["build_manager"],
"ete":        ["build_manager"],
"production": ["build_manager", "cluster_operator"],
```

---

## 8. Key models

### ClusterSpec

```python
class ClusterSpec(BaseModel):
    name: str
    platform: Optional[PlatformSpec] = None   # null for externally-managed clusters
    vip: str                                  # control plane VIP
    ip_range: str                             # e.g. "192.168.4.160-192.168.4.169"
    dimensions: ClusterDimensions             # control_plane_count, worker_count, cpu, memory, disk
    managed_gitops: bool = True               # True → create {cluster}-infra/apps repos on POST
    gitops_repo_url: Optional[str] = None     # set by create_cluster when managed_gitops=True
    sops_secret_ref: str                      # K8s Secret name holding SOPS age key
    extra_manifests: List[str] = []           # Talos extra_manifests URLs (cilium, flux, gateway-api)
    bastion: Optional[BastionSpec] = None     # rewrites kubeconfig server URL
    allow_scheduling_on_control_planes: bool = False  # required when worker_count=0
    external_hosts: List[str] = []            # FQDNs for cert-manager SANs + Gateway listeners
```

`PlatformSpec` (Proxmox hypervisor):

| Field | Type | Default | Maps to cluster-chart | Notes |
| --- | --- | --- | --- | --- |
| `name` | str | required | — | Human identifier (e.g. `"venus"`, `"saturn"`) |
| `type` | str | `"proxmox"` | — | Only `"proxmox"` supported |
| `endpoint` | str | required | — | Proxmox API URL (e.g. `"https://192.168.4.50:8006"`) |
| `nodes` | List[str] | required | `proxmox.allowed_nodes` | Proxmox node names that may schedule VMs |
| `template_node` | Optional[str] | `nodes[0]` | `proxmox.template.sourcenode` | Node holding the VM clone template |
| `template_vmid` | int | `100` | `proxmox.template.template_vmid` | VM template ID on `template_node` |
| `credentials_ref` | str | `"capmox-manager-credentials"` | `ProxmoxCluster.credentialsRef.name` | K8s secret with CAPMOX API credentials |
| `bridge` | str | `"vmbr0"` | `proxmox.vm.bridge` | Proxmox VM network bridge |

### ApplicationSpec

```python
class ApplicationSpec(BaseModel):
    name: str
    cluster: str           # target cluster name — used for repo routing
    helm_repo_url: str
    chart_name: str
    chart_version: str
    values_yaml: str = ""
    app_repo_url: Optional[str] = None
```

### ApplicationClusterConfig

```python
class ApplicationClusterConfig(BaseModel):
    app_id: str            # application name
    cluster_id: str        # cluster name
    chart_version_override: Optional[str] = None
    values_override: str = ""
    enabled: bool = True
    pipeline_stage: Optional[str] = None   # dev | ete | production
    gitops_source_ref: Optional[str] = None  # external GitRepository CR name
    external_hosts: List[str] = []          # subset of cluster FQDNs routed to this app
```

Config ID is `{app_id}-{cluster_id}`.

---

## 9. Test conventions

### Fixture structure

- `tests/conftest.py` — shared: `client` fixture, `headers_for()`, sample payloads (`CLUSTER_SPEC`, `APP_SPEC`, `APP_CONFIG_SPEC`, `PIPELINE_SPEC`)
- `tests/test_routers/` — HTTP-level tests using `TestClient`; test role enforcement and response shapes; service methods patched via `unittest.mock.patch`
- `tests/test_services/` — Service unit tests; service instantiated, then `svc._git = AsyncMock()` / `svc._gh = AsyncMock()` injected
- `tests/test_models/` — Pydantic model validation tests
- `tests/test_auth.py` — Auth middleware tests
- `tests/integration/` — Integration tests (run against local dev server, not part of default pytest run)

### Mocking pattern for services

```python
from unittest.mock import AsyncMock
from gitopsgui.services.cluster_service import ClusterService

svc = ClusterService()
svc._git = AsyncMock()
svc._gh = AsyncMock()
svc._git.read_file.return_value = "cluster: {name: test}\n"
result = await svc.get_cluster("test")
```

### Mocking pattern for routers

```python
with patch(
    "gitopsgui.api.routers.clusters.ClusterService.create_cluster",
    new=AsyncMock(return_value=_CLUSTER_RESPONSE),
):
    r = client.post("/api/v1/clusters", json=CLUSTER_SPEC, headers=CLUSTER_OP_HEADERS)
assert r.status_code == 202
```

### Test data files

`tests/test_data/` holds JSON payloads used in integration tests and as reference fixtures:

```
tests/test_data/
├── clusters/            — e.g. gitopsdev-create.json, platform-services-create.json
├── applications/        — e.g. nexus.json, keycloak.json, forgejo.json
├── application-configs/ — e.g. nexus-platform-services.json
└── pipelines/
```

Use these when running manual `curl` tests against the local dev server or as reference for new test data.

---

## 10. How to add a new endpoint

1. **Model**: add Pydantic model(s) to `src/gitopsgui/models/<resource>.py`.
2. **Service**: add methods to `src/gitopsgui/services/<resource>_service.py`. Constructor: `self._git = GitService()` / `self._gh = GitHubService()` if using management repo; call `repo_router` helpers if per-cluster.
3. **Router**: add route to `src/gitopsgui/api/routers/<resource>.py`. Import `require_role` from `..auth`. Use `_=require_role(...)` for fire-and-forget auth, `caller=require_role(...)` when you need the username or role.
4. **Register**: if new router file, add `app.include_router(...)` in `main.py`.
5. **Tests**:
   - Router test in `tests/test_routers/test_<resource>.py`: test allowed roles, rejected roles, 404 shape, 202/201 shape.
   - Service test in `tests/test_services/test_<resource>_service.py`: inject `AsyncMock` git/gh; test each method.
6. **Run tests**: `.venv/bin/pytest -q --tb=short`

---

## 11. How to add a new service

Follow the pattern in `cluster_service.py` or `app_config_service.py`:

- Constructor sets `self._git` and `self._gh` (or leaves them `None` for repo_router-based routing).
- All git I/O is `async` via `GitService` methods (which use `asyncio.to_thread` internally).
- Writes always go: `create_branch` → `write_file` × N → `commit` → `push` → `create_pr`.
- Return a response model with `pr_url` populated.
- Test by injecting `svc._git = AsyncMock()` — do not call real git in unit tests.

---

## 12. How to test against the live API

The live API runs on gitopsdev cluster (when deployed). Access depends on network:

- With Cloudflare WARP: `https://gitopsgui.podzone.cloud`
- Without WARP (direct): `kubectl --context gitopsdev-admin@gitopsdev --server=https://192.168.1.80:6442 port-forward ...`

For local dev server testing:

```bash
export $(cat .env.local | xargs)
.venv/bin/uvicorn gitopsgui.api.main:app --reload --port 8000

# In another terminal:
curl -s -X GET http://localhost:8000/api/v1/clusters \
  -H "X-Forwarded-User: testuser" \
  -H "X-Auth-Request-Groups: cluster-operators" | jq .

curl -s -X POST http://localhost:8000/api/v1/clusters \
  -H "Content-Type: application/json" \
  -H "X-Forwarded-User: testuser" \
  -H "X-Auth-Request-Groups: cluster-operators" \
  -d @tests/test_data/clusters/platform-services-create.json | jq .
```

**API-first testing protocol**: before calling any write endpoint in testing, document all expected object attributes (fields, types, defaults, constraints) and review them. This catches schema gaps before roundtrip failures in Flux or CAPI.

---

## 13. Deployment

### Image build (on erectus, 192.168.1.201)

```bash
# From local machine:
rsync -az --delete src/ colleymj@192.168.1.201:~/gitopsapi-build/src/

# On erectus:
docker build -t ghcr.io/motttt/gitopsapi:vX.Y.Z -f Dockerfile.api-only .
docker push ghcr.io/motttt/gitopsapi:vX.Y.Z
```

The image must be public on ghcr.io (or a `ghcr-pull-secret` imagePullSecret must be configured). As of 2026-03-19, the package was private with a pull blocker — see QUESTIONS.md [CC-078].

Current deployed image: `v0.1.6` (chart 0.1.3). Update `charts/gitopsapi/values.yaml` `image.tag` when bumping.

### Helm chart publish

The chart is published as a static GitHub Pages Helm repo at `https://motttt.github.io/gitopsapi`:

```bash
helm package charts/gitopsapi/
cp gitopsapi-0.1.X.tgz docs/
helm repo index docs/ --url https://motttt.github.io/gitopsapi
git add docs/ gitopsapi-0.1.X.tgz
git commit -m "chore: publish chart 0.1.X"
git push
```

The `docs/index.yaml` is the Helm repo index. The HelmRepository CR in the cluster points to `https://motttt.github.io/gitopsapi`.

### Flux on gitopsdev

The HelmRelease is `gitopsapi/gitopsapi-gitopsapi` in the `gitopsdev` cluster:

```bash
kubectl --context gitopsdev-admin@gitopsdev --server=https://192.168.1.80:6442 \
  get helmrelease -n gitopsapi

flux --context gitopsdev-admin@gitopsdev reconcile hr gitopsapi -n flux-system
```

HelmRelease + HelmRepository manifests: `docs/deploy/gitopsapi.yaml`. Values ConfigMap: `docs/deploy/gitopsapi-values.yaml` (NOTE: this file is the old in-repo version; live values come from the cluster's ConfigMap).

Secrets required on the cluster:

- `gitopsapi-github-token` — key `token`, value is GitHub PAT
- `gitopsapi-mgmt-kubeconfig` — key `kubeconfig`, value is kubeconfig YAML
- `gitopsapi-age-key` — key `age.agekey`, value is SOPS age private key
- `ghcr-pull-secret` — type `kubernetes.io/dockerconfigjson` for image pull (if image is private)

---

## 14. Known gaps — do not implement without a task

These are documented gaps, not bugs. Do not fix without an explicit task assignment:

| Gap | Description | Label |
|---|---|---|
| `list_applications()` / `get_application(name)` | Returns empty in multi-repo prod — no cluster registry to enumerate all clusters | CC-055 |
| `list_by_application(app_id)` in AppConfigService | Same root cause — no cluster registry | CC-055 |
| Deploy key automation (TR-039a) | Manual steps: generate SSH key pair, register on GitHub, write K8s Secret | CC-053 |
| SOPS key lifecycle (TR-039b) | `sops-bootstrap` endpoint exists but per-cluster automation incomplete | CC-053 |
| Cluster registry | Needed to enable cross-cluster list operations | CC-055 |
| Hypervisor routing/validation | `platform` field drives cluster-chart `proxmox:` section; no API-level validation that the named hypervisor is reachable | PROJ-003/T-010 ✅ |

---

## 15. Active tasks and communication

**Read tasks from**: `/Users/martincolley/workspace/podzoneAgentTeam/planning/team-tasklist.md`
Filter for `Claude-Code` in the Agent column. Tasks marked `🚀 Ready` are next up; `⛔ Blocked` need their blocker resolved first.

**Write questions/blockers to**: `QUESTIONS.md` in this repo (monitored by Team Lead).

**Escalate to Team Lead**: create `podzoneAgentTeam/agents/team-lead/incoming/YYYY-MM-DD-{task}.md`.

**Specs**: `podzoneAgentTeam/specifications/gitopsgui-requirements.md` (v0.4). API schema: `podzoneAgentTeam/specifications/gitopsapi-schema.md`.

**Working branch convention**: `{resource}/{action}-{name}-{8-char-uuid}` — e.g. `cluster/provision-security-a1b2c3d4`. Never commit directly to main.

---

## 16. Frequent gotchas

| Gotcha | Detail |
|---|---|
| `Depends(require_role(...))` | Double-wraps — use `_=require_role(...)` directly |
| `repo_url=""` | Does not fall back to env var — raises `RuntimeError` on first git use |
| `GITOPS_SKIP_INIT=1` with missing local repo | Guard checks for `.git/` presence — will attempt clone if absent, which fails without network |
| `get_user()` vs `get_user(owner)` | `get_user()` = authenticated user; `get_user(owner)` = lookup by login — use no-args form when creating repos for the authenticated account |
| HTTPS vs SSH | All repo URLs are HTTPS in prod (v0.1.6+). SSH was removed. `GITHUB_TOKEN` is injected into the URL by `_auth_url()` |
| `managed_gitops=False` | Skips GitHub repo creation in `create_cluster`; useful for externally-managed clusters and in tests |
| Service tests need `AsyncMock` not `MagicMock` | Git/GitHub service methods are all `async` |
| `conftest.py` sets env vars at module import time | Do not rely on env from shell when running tests — conftest overrides take precedence |
| `allow_scheduling_on_control_planes` | Must be `True` when `worker_count=0` (single-node or CP-only cluster); the cluster-chart conditionally sets a Talos `MachineDeployment` |
| Chart version in `_render_cluster_yaml` | Hardcoded to `0.1.20` in `cluster_service.py` — update when bumping cluster-chart version |
