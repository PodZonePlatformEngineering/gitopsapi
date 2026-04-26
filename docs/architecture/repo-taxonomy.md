# gitopsapi — Repository Taxonomy

**Status:** Design / Specification
**Author:** Hephaestus (Claude Code)
**Date:** 2026-03-29
**Task:** PROJ-003/T-027

---

## Purpose

This document is the canonical reference for every repository type in the gitopsapi
ecosystem. It supersedes the legacy cluster09 mono-repo assumptions and provides the
vocabulary for multi-instance, multi-hypervisor, multi-cluster ETE topology.

Read alongside: `zero-trust-bootstrap.md` (trust model), `promotion-pipeline.md`
(PR-governed stage promotion).

---

## Repository Types

### 1. gitopsapi instance repo

**Example (podzone dev):** `PodZonePlatformEngineering/management-infra`
**Owner:** gitopsapi instance (writes), Flux on the management cluster (reads)
**Env var:** `GITOPS_REPO_URL`

The root of trust for a single gitopsapi installation. Contains:

```
management-infra/
  clusters/
    management/
      infrastructure.yaml     # Flux Kustomizations for the management cluster
      clusters.yaml            # CAPI Cluster objects (one per provisioned workload cluster)
  gitops/
    gitops-management/        # Flux-reconciled manifests for the management cluster
      02-network/
        cloudflare-tunnel-token.sops.yaml   # SOPS-encrypted Cloudflare tunnel token
        cloudflared.yaml                    # cloudflared HelmRelease
  sops-keys/
    management.agekey          # Age private key for this instance (NEVER committed)
  .sops.yaml                   # Public key recipient config (committed)
```

**Uniqueness:** One per gitopsapi instance. The gitopsapi instance and the Flux
instance on the management cluster are tightly coupled to this repo.

**Key principle:** This repo is the root of trust. Per-cluster SOPS private keys are
stored encrypted under the instance key. Instance recovery recovers all per-cluster keys.

---

### 2. cluster-infra repo

**Example:** `PodZonePlatformEngineering/gitopsdev-infra`, `PodZonePlatformEngineering/gitopsete-infra`
**Owner:** gitopsapi writes, Flux on the workload cluster reads
**Naming convention:** `{cluster-name}-infra`

Flux manifests for a single workload cluster. Created by `POST /clusters` and populated
by subsequent wire endpoints. Contains:

```
{cluster}-infra/
  gitops/
    gitops-infra/             # Shared infrastructure layer (CNI, cert-manager, etc.)
    gitops-gateway/           # GatewayClass, Gateway, ClusterIssuer, Certificate
    gitops-storage/           # democratic-csi NFS/iSCSI StorageClass HelmReleases
    gitops-apps/              # Application HelmReleases (one per deployed application)
```

**Uniqueness:** One per workload cluster. gitopsapi creates this repo on first
`POST /clusters` call.

---

### 3. cluster-apps repo

**Example:** `PodZonePlatformEngineering/gitopsdev-apps`, `PodZonePlatformEngineering/gitopsete-apps`
**Owner:** gitopsapi writes, Flux on the workload cluster reads
**Naming convention:** `{cluster-name}-apps`

Application Kustomizations and HelmRelease values overrides for a single workload
cluster. Separated from cluster-infra to allow application promotion independent of
infrastructure changes.

**Uniqueness:** One per workload cluster. gitopsapi creates this repo on first
`POST /application-deployments` call.

---

### 4. shared-infra repo

**Example:** `PodZonePlatformEngineering/shared-infra`
**Owner:** cluster operator maintains; multiple clusters read
**Env var:** referenced by Kustomization `sourceRef` in the management cluster's
`infrastructure.yaml`

Shared Flux manifests applied identically across multiple clusters (CNI configuration,
cert-manager, Cilium, Gateway API CRDs). Not managed by gitopsapi at runtime — cluster
operator maintains this repo manually.

**Uniqueness:** Typically one per site/organisation. May be versioned or branched for
environment variants (dev vs prod Cilium config).

---

### 5. cluster-charts repo

**Example:** `PodZonePlatformEngineering/cluster-charts`
**Owner:** cluster operator / gitopsapi developer
**Env vars:** `GITOPS_CLUSTER_CHART_REPO_URL`, `GITOPS_CLUSTER_CHART_REPO_NAME`
(default: `cluster-charts`), `GITOPS_CLUSTER_CHART_VERSION` (default: `0.1.20`)

Helm chart repository (gh-pages served) containing the `cluster-chart` Helm chart.
gitopsapi reads this when generating CAPI HelmRelease manifests for new clusters. The
chart encodes the CAPI/CAPMOX/CABPT provider version assumptions for a specific
management cluster configuration.

**Uniqueness:** One per organisation. May diverge between dev and ete instances if
provider versions differ — controlled via `GITOPS_CLUSTER_CHART_VERSION`.

**Source:** `PodZonePlatformEngineering/cluster-chart` (chart source)
Published via OCI CI/CD pipeline (`publish-chart.yml`); see repo CI for current registry URL.

---

### 6. app catalog repo

**Example:** `PodZonePlatformEngineering/gitopsapi-apps`
**Owner:** cluster operator / gitopsapi developer
**Env var:** `GITOPS_CATALOG_REPO_URL`

Public read-only repository containing application templates and catalog entries.
gitopsapi reads this when fulfilling `POST /application-deployments` to resolve the
application's HelmRelease template. Contains:

```
gitopsapi-apps/
  catalog/
    {app-name}/
      catalog.yaml             # Application metadata (chart URL, version, description)
      values.yaml              # Default HelmRelease values
```

**Uniqueness:** One per organisation (may be shared across instances). The catalog
is read-only at runtime — gitopsapi never writes to it.

---

### 7. gitopsapi source repo

**Example:** `PodZonePlatformEngineering/gitopsapi`
**Owner:** development team

The gitopsapi application source code. Not managed by gitopsapi at runtime. Deployed
to the management cluster via a HelmRelease in the instance repo (self-hosted mode) or
directly via `helm install` (bootstrap mode).

---

### 8. gitopsgui source repo

**Example:** `PodZonePlatformEngineering/gitopsgui`
**Owner:** development team

The React frontend for gitopsapi. Not managed by gitopsapi at runtime. Deployed
alongside gitopsapi in the management cluster.

---

## Functionality Matrix

For each gitopsapi capability, the repos that must be present and configured:

| Capability | instance repo | cluster-infra | cluster-apps | shared-infra | cluster-charts | catalog |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| Provision a cluster | ✅ required | created | created | optional | ✅ required | — |
| Bootstrap cluster SOPS key | ✅ required (`sops-keys/`) | — | — | — | — | — |
| Wire Cloudflare tunnel | ✅ required | — | — | — | — | — |
| Wire storage classes | — | ✅ required | — | — | — | — |
| Wire gateway | — | ✅ required | — | — | — | — |
| Deploy an application | — | ✅ required | ✅ required | — | — | ✅ required |
| Promote app dev→ete | — | both stages | both stages | — | — | ✅ required |
| Serve gitopsapi itself | ✅ required | — | — | — | — | — |
| Full multi-instance ETE | one per instance | one per cluster | one per cluster | shared | one per instance | shared |

---

## ETE Topology

The podzone ETE environment uses two independent gitopsapi instances, one per
hypervisor. Each instance has its own repos and SOPS key.

```
┌─────────────────────────────────────────────────────────────────────┐
│  venus (hypervisor — dev)           saturn (hypervisor — ete)       │
│                                                                     │
│  ┌─────────────────────┐            ┌─────────────────────┐        │
│  │  management cluster │            │  management-ete      │        │
│  │  (gitopsapi-dev)    │            │  cluster             │        │
│  │                     │            │  (gitopsapi-ete)     │        │
│  │  reads:             │            │                     │        │
│  │  management-infra ◄─┼──────┐     │  reads:             │        │
│  │                     │      │     │  management-ete-infra│        │
│  └─────────────────────┘      │     └─────────────────────┘        │
│                                │                                    │
│  Manages workload clusters:    │     Manages workload clusters:     │
│  gitopsdev (venus)             │     gitopsete (saturn)             │
│  musings   (venus)             │     gitopsprod (saturn)            │
│                                │                                    │
└────────────────────────────────┼─────────────────────────────────── ┘
                                 │
                    ┌────────────┴─────────────┐
                    │  Shared across instances  │
                    │  - shared-infra           │
                    │  - cluster-charts         │
                    │  - gitopsapi-apps catalog │
                    └──────────────────────────┘
```

### Repo naming convention for ETE

| Repo type | Dev instance | ETE instance |
|---|---|---|
| Instance repo | `management-infra` | `management-ete-infra` |
| Cluster-infra | `gitopsdev-infra` | `gitopsete-infra` |
| Cluster-apps | `gitopsdev-apps` | `gitopsete-apps` |
| SOPS key | `management.agekey` | `management-ete.agekey` |

---

## GitOpsAPIConfig Gaps

The following configuration values are **env var only** — they cannot be set or
inspected via the API at runtime. Each represents a gap in the zero-trust bootstrap
model (see `zero-trust-bootstrap.md` Phase 3).

| Config | Env var | Current default | Gap |
|---|---|---|---|
| Instance repo URL | `GITOPS_REPO_URL` | `""` (must be set) | Must be in `GitOpsAPIConfig` + `GitRepo` model; settable via Phase 3 API |
| Instance repo SSH key | `GITOPS_SSH_KEY_PATH` | `/etc/gitops-ssh/id_rsa` | Must be a `GitRepo` credential ref; mounted from K8s Secret |
| App catalog repo URL | `GITOPS_CATALOG_REPO_URL` | `""` (must be set) | Must be in `GitOpsAPIConfig` |
| Cluster-charts Helm repo URL | `GITOPS_CLUSTER_CHART_REPO_URL` | `""` | Must be in `GitOpsAPIConfig` |
| Cluster-charts repo name | `GITOPS_CLUSTER_CHART_REPO_NAME` | `cluster-charts` | Must be in `GitOpsAPIConfig` |
| Cluster-chart version | `GITOPS_CLUSTER_CHART_VERSION` | `0.1.20` | Must be in `GitOpsAPIConfig` |
| Management kubeconfig secret | `MGMT_KUBECONFIG_SECRET` | `""` (must be set) | Must be a Phase 3 API input; stored as K8s Secret; writable via `POST /bootstrap/configure` |

### Proposed `GitOpsAPIConfig` extension

```python
class GitOpsAPIConfigUpdate(BaseModel):
    name: Optional[str] = None
    forge_ids: Optional[List[str]] = None
    admin_password: Optional[str] = None

    # Phase 3 bootstrap inputs — currently env var only
    instance_repo_url: Optional[str] = None       # GITOPS_REPO_URL
    catalog_repo_url: Optional[str] = None        # GITOPS_CATALOG_REPO_URL
    cluster_chart_repo_url: Optional[str] = None  # GITOPS_CLUSTER_CHART_REPO_URL
    cluster_chart_repo_name: Optional[str] = None # GITOPS_CLUSTER_CHART_REPO_NAME
    cluster_chart_version: Optional[str] = None   # GITOPS_CLUSTER_CHART_VERSION
```

**Migration path:** on startup, read env vars as before; if `GitOpsAPIConfig` K8s
ConfigMap contains a value for the same field, prefer the stored value. On
`PUT /config`, update the ConfigMap and log the change. This preserves backwards
compatibility with existing env-var-based deployments.

---

## cluster09 Legacy — Retired Assumptions

The following assumptions from the cluster09 mono-repo era are explicitly retired.
Any code, config, or documentation referencing these patterns should be updated.

| Legacy assumption | Retirement date | Replacement |
|---|---|---|
| Single repo contains infra + apps + bootstrap for all clusters | 2026-03-22 | Separate per-cluster `-infra` and `-apps` repos; instance repo for CAPI bootstrap |
| `GITOPS_REPO_URL` points at a mono-repo | 2026-03-22 | `GITOPS_REPO_URL` is the instance repo (management-infra) only |
| ClusterService writes cluster manifests into the same repo as everything else | 2026-03-22 | Instance repo = CAPI CRDs only; `{cluster}-infra` = Flux manifests for that cluster |
| Trismagistus read-only access to cluster09 as source of truth | 2026-03-29 | Trismagistus switched off; ETE setup removes cluster09 coupling entirely |
| `podzone-charts` as hardcoded Helm repo name | 2026-03-29 | `GITOPS_CLUSTER_CHART_REPO_NAME` env var (default: `cluster-charts`); configurable |

---

## Open Items

- [ ] `GitOpsAPIConfig` extension (proposed above) — implement as PROJ-003 task
- [ ] Management kubeconfig Phase 3 API input — `POST /bootstrap/configure` must
  accept kubeconfig and store as K8s Secret; see `zero-trust-bootstrap.md` Phase 3
- [ ] ETE instance repo provisioning — `management-ete-infra` must be created and
  bootstrapped on saturn before ETE testing (PROJ-012/Setup/T-002)
- [ ] Shared-infra variant strategy — does ETE use same `shared-infra` as dev, or
  a fork? CNI config (IP pool) differs per environment
- [ ] Catalog repo versioning — no mechanism to pin catalog repo to a specific ref;
  currently reads HEAD of default branch

---

*See also:* `zero-trust-bootstrap.md` (trust model and bootstrap phases),
`promotion-pipeline.md` (PR-governed stage promotion across cluster-infra/cluster-apps repos)
