# Nexus Image Cache Migration

**Task**: PROJ-010/T-008
**Status**: Proxy repos configured; external routing pending (artefacts.podzone.cloud HTTPRoute + DNS)
**Unblocks**: PROJ-010/T-004 (Harbor VM decommission) — after external routing confirmed working
**Date**: 2026-03-26 (updated; originally 2026-03-21)

---

## Summary

Harbor (`harbor.podzone.cloud`, VM at `192.168.4.100`) has been decommissioned as the
cluster image cache. Nexus replaces it as a caching pull-through proxy. Own application
images (gitopsapi, etc.) are pushed to GHCR (`ghcr.io/motttt/...`).

This document records:

1. All files that reference `harbor.podzone.cloud` or `192.168.4.100` as an image registry
2. The scope of the Talos registry mirror configuration that pointed clusters at Harbor
3. Required Nexus proxy repository setup
4. Migration steps per manifest

---

## Section 1 — Harbor References Found

### 1.1 Active Manifests

#### A. Test data — gitopsapi application create payload

| Field | Value |
|---|---|
| File | `/Users/martincolley/workspace/code/gitopsapi/tests/test_data/applications/gitopsapi-create.json` |
| Line | 10 |
| Reference | `harbor.podzone.cloud/gitopsapi/gitopsapi` (in `values_yaml` → `image.repository`) |
| Status | **Must update** — this is a test fixture that exercises the POST /api/v1/applications endpoint |

Current value:
```
image.repository: harbor.podzone.cloud/gitopsapi/gitopsapi
```

Correct value:
```
image.repository: ghcr.io/motttt/gitopsapi
```

Note: The gitopsapi chart `values.yaml` default already uses `ghcr.io/motttt/gitopsapi`
(confirmed at `charts/gitopsapi/values.yaml` line 2). The test data file has a stale value
from before the GHCR migration decision. This is a test fixture, not a deployed manifest, but
it should reflect the correct registry to avoid misleading future testers.

---

#### B. Cluster chart — Talos registry mirrors (cluster09)

| Field | Value |
|---|---|
| File | `/Users/martincolley/workspace/cluster09/cluster-chart/values.yaml` |
| Lines | 45–75 |
| Reference | `192.168.4.100` (Harbor VM IP) as mirror endpoint for all upstream registries |

This is the Helm chart template used by CAPI to provision Talos clusters. The `registries`
block in `values.yaml` configures Talos's `machine.registries.mirrors` for new clusters.
The existing configuration points to Harbor:

```yaml
registries:
  config:
    192.168.4.100:
      auth:
        username: admin
        password: Harbor12345
  mirrors:
    docker.io:
      endpoints:
        - http://192.168.4.100/v2/proxy-docker.io
      overridePath: true
    ghcr.io:
      endpoints:
        - http://192.168.4.100/v2/proxy-ghcr.io
      overridePath: true
    gcr.io:
      endpoints:
        - http://192.168.4.100/v2/proxy-gcr.io
      overridePath: true
    registry.k8s.io:
      endpoints:
        - http://192.168.4.100/v2/proxy-registry.k8s.io
      overridePath: true
    quay.io:
      endpoints:
        - http://192.168.4.100/v2/proxy-quay.io
      overridePath: true
    cr.fluentbit.io:
      endpoints:
        - http://192.168.4.100/v2/proxy-cr.fluentbit.io
      overridePath: true
```

All six mirror endpoints must be updated to Nexus URLs once Nexus proxy repositories are
configured and tested (see Section 3).

Note: This `registries` block is templated into both `talosconfigtemplate.yaml` and
`taloscontrolplane.yaml` via `{{- with .Values.registries }}`. Updating `values.yaml`
changes the default for all future clusters provisioned from this chart; **existing clusters
are not updated automatically** — their Talos machine config would need to be patched
separately (outside Flux, via `talosctl`).

---

### 1.2 Stale / Decommissioned References (not requiring action)

These files reference Harbor but are either already decommissioned or documentation-only:

| File | Reference | Notes |
|---|---|---|
| `/Users/martincolley/workspace/cluster09/gitops/gitops-apps/harbor/harbor.yaml` | HelmRelease for harbor chart | Stale — cluster09 gitops/ is dead (no cluster watches it). Candidate for removal per 2026-03-18-cluster09-gitops-cleanup.md |
| `/Users/martincolley/workspace/cluster09/gitops/gitops-apps/harbor/harbor-values.yaml` | `harbor.podzone.cloud` hostname, `externalURL` | Same — stale gitops/ content |
| `/Users/martincolley/workspace/cluster09/clusters/ManagementCluster/apps.yaml` | Harbor Kustomization (commented out) | Already commented out |
| `/Users/martincolley/workspace/cluster09/docs/Harbor.md` | Installation instructions for Harbor VM | Documentation only — keep for historical context |
| `/Users/martincolley/workspace/cluster09/site/Harbor/index.html` | Generated site from Harbor.md | Documentation only |
| `/Users/martincolley/workspace/code/gitopsapi-apps/catalog/application-catalog.md` | Harbor and Nexus catalog entries | Correctly documents Harbor as decommissioning |
| `/Users/martincolley/workspace/code/gitopsdocs/schemas/application-catalogue.md` | Harbor catalog entry | Documentation — no action needed |
| `/Users/martincolley/workspace/code/podzone-infrastructure/config/freyr-port-forwards.yaml` | `harbor-http` forward to `192.168.4.100:80` on port 8080 | Harbor VM still powered on; this forward can be removed when Harbor VM is decommissioned (PROJ-010/T-004) |

---

### 1.3 No Harbor References Found

The following were checked and contain **no** `harbor.podzone.cloud` or `192.168.4.100`
image references:

- All active cluster-repos YAML: `gitopsdev-apps`, `gitopsdev-infra`, `gitopsete-*`,
  `gitopsprod-*`, `openclaw-apps`, `openclaw-infra`, `observability-*`, `agentsonly-infra`,
  `management-infra`, `shared-infra`
- The actual deployed gitopsapi values (`gitopsdev-apps/gitops/gitops-apps/gitopsapi/gitopsapi-values.yaml`)
  already uses `ghcr.io/motttt/gitopsapi`
- The gitopsapi Helm chart default (`charts/gitopsapi/values.yaml`) uses `ghcr.io/motttt/gitopsapi`
- The gitopsapi source code (`src/gitopsgui/`) — no registry references
- The gitopsapi-apps catalog templates (`templates/applications/application.json`, etc.)

---

## Section 2 — Registries Being Proxied by Harbor

Based on the cluster-chart `values.yaml` and Harbor documentation, Harbor was proxying
the following upstream registries for Talos cluster nodes:

| Upstream Registry | Harbor Proxy Project | Used By |
|---|---|---|
| `docker.io` | `proxy-docker.io` | General workloads (e.g. chromedp/headless-shell, prometheus/alertmanager) |
| `ghcr.io` | `proxy-ghcr.io` | Flux controllers, kubelet-serving-cert-approver, gitopsapi, openclaw/openclaw, podzone-mpc |
| `gcr.io` | `proxy-gcr.io` | Legacy Google Container Registry (less used now) |
| `registry.k8s.io` | `proxy-registry.k8s.io` | metrics-server, Kubernetes sig-storage (CSI snapshotter) |
| `quay.io` | `proxy-quay.io` | piraeus-operator, piraeus-server, cilium, cilium-envoy, cilium-operator-generic, hubble-relay, hubble-ui, prometheus-operator, prometheus |
| `cr.fluentbit.io` | `proxy-cr.fluentbit.io` | Fluent Bit (logging) |

---

## Section 3 — Required Nexus Proxy Repository Configuration

Nexus is already deployed to `gitopsdev` cluster via HelmRelease
(`gitopsdev-apps/gitops/gitops-apps/nexus/`). The HelmRelease is defined but Nexus proxy
repositories are **not yet configured** — the `nexus-values.yaml` only sets resource limits
and storage; no proxy repo setup is automated.

### 3.1 Proxy Repository Setup (manual, via Nexus UI or REST API)

Create the following Docker proxy repositories in Nexus:

| Repository Name | Remote URL | Notes |
|---|---|---|
| `proxy-dockerhub` | `https://registry-1.docker.io` | Docker Hub — set v1 API compatibility, configure Docker Hub credentials if rate-limited |
| `proxy-ghcr` | `https://ghcr.io` | GitHub Container Registry — anonymous pulls work; add GitHub PAT for private repos |
| `proxy-gcr` | `https://gcr.io` | Google Container Registry — anonymous for public images |
| `proxy-k8s` | `https://registry.k8s.io` | Kubernetes registry |
| `proxy-quay` | `https://quay.io` | Quay.io — used heavily by Cilium and Piraeus |
| `proxy-fluentbit` | `https://cr.fluentbit.io` | Fluent Bit registry |

For each repository:
- **Type**: Docker (proxy)
- **HTTP connector**: enable port 5000 (or use the path-based connector if Nexus is behind
  a reverse proxy/gateway)
- **Allow anonymous docker pull**: enable
- **Blob store**: use the default or a dedicated blob store on `piraeus-datastore` PVC
- **Remote URL**: as listed above
- **Negative cache**: enable (TTL 1440 min) to avoid hammering upstream for missing images

### 3.2 Nexus Hostname

The planned external hostname is `artefacts.podzone.cloud` (already listed as an
`external_host` in `management-infra/gitops/cluster-charts/platform-services/platform-services-values.yaml`
line 21). The Nexus HelmRelease in `gitopsdev-apps` does not yet configure an ingress or
HTTPRoute — this is a gap that needs addressing before clusters can use Nexus as a mirror.

Until `artefacts.podzone.cloud` is routed to Nexus, the cluster-internal URL
(e.g. `http://nexus.nexus.svc.cluster.local:8081`) is only reachable from within the
gitopsdev cluster. For cross-cluster use as a registry mirror, an external URL accessible
from all cluster nodes is required.

### 3.3 Nexus Docker Registry URL format

Nexus exposes Docker repositories in two ways:
- **Port-based**: each proxy repo on its own port (e.g., `nexus.podzone.cloud:5001` for
  Docker Hub proxy, `nexus.podzone.cloud:5002` for GHCR proxy, etc.)
- **Path-based** (requires Nexus Repository Manager Pro, or use of a reverse proxy with
  path rewriting): `nexus.podzone.cloud/v2/<repo-name>/...`

The Talos `registries.mirrors` format uses the endpoint URL with `overridePath: true`.
For Nexus Docker proxy repositories accessed via a gateway, the endpoint format is:

```
http://artefacts.podzone.cloud/v2/<repo-name>
```

or with port-based:

```
http://artefacts.podzone.cloud:<port>
```

---

## Section 4 — Migration Steps

### Step 1 — Configure Nexus proxy repositories ✅ Complete 2026-03-26

Completed via Nexus REST API from within the pod (credential stays in-cluster). All six repos
created (HTTP 201): `proxy-dockerhub`, `proxy-ghcr`, `proxy-gcr`, `proxy-k8s`, `proxy-quay`,
`proxy-fluentbit`. All are Docker proxy type, backed by the `default` blob store.

Access is path-based at port 8081 (the only port exposed by the K8s Service):
`http://artefacts.podzone.cloud/repository/{repo-name}` with `overridePath: true` in Talos
mirror config — same pattern as Harbor.

### Step 2 — Expose Nexus externally

Add a HTTPRoute (or Gateway API HTTPRoute) to expose Nexus at `artefacts.podzone.cloud`.
Update `gitopsdev-apps/gitops/gitops-apps/nexus/nexus-values.yaml` to add an ingress or
expose configuration, then add the nexus app to the kustomization.yaml (note: the nexus
app directory exists but is not yet included in
`gitopsdev-apps/gitops/gitops-apps/kustomization.yaml`).

### Step 3 — Test proxy pulls

From a cluster node or a pod with network access to `artefacts.podzone.cloud`, test:

```bash
# Test Docker Hub proxy
docker pull artefacts.podzone.cloud/v2/proxy-dockerhub/library/busybox:latest

# Test GHCR proxy
docker pull artefacts.podzone.cloud/v2/proxy-ghcr/fluxcd/source-controller:v1.6.0

# Test quay.io proxy
docker pull artefacts.podzone.cloud/v2/proxy-quay/cilium/cilium:v1.17.4
```

Confirm images are served from Nexus cache (check Nexus browse UI shows the cached blobs).

### Step 4 — Update the cluster-chart values.yaml ✅ Complete 2026-03-26

Once Nexus URL is confirmed, update
`/Users/martincolley/workspace/cluster09/cluster-chart/values.yaml` lines 45–75:

```yaml
registries:
  config:
    artefacts.podzone.cloud:
      auth:
        username: admin
        password: <nexus-admin-password>    # store in cluster secret, not plaintext
  mirrors:
    docker.io:
      endpoints:
        - http://artefacts.podzone.cloud/v2/proxy-dockerhub
      overridePath: true
    ghcr.io:
      endpoints:
        - http://artefacts.podzone.cloud/v2/proxy-ghcr
      overridePath: true
    gcr.io:
      endpoints:
        - http://artefacts.podzone.cloud/v2/proxy-gcr
      overridePath: true
    registry.k8s.io:
      endpoints:
        - http://artefacts.podzone.cloud/v2/proxy-k8s
      overridePath: true
    quay.io:
      endpoints:
        - http://artefacts.podzone.cloud/v2/proxy-quay
      overridePath: true
    cr.fluentbit.io:
      endpoints:
        - http://artefacts.podzone.cloud/v2/proxy-fluentbit
      overridePath: true
```

This change affects **future cluster provisioning** only. Existing running clusters will
continue using whatever mirror was baked into their Talos machine config at provisioning
time.

### Step 5 — Patch existing clusters (if needed)

If Harbor is being powered off before existing clusters are reprovisioned, apply the new
`registries` config to running clusters via:

```bash
talosctl --context gitopsdev-admin@gitopsdev \
  patch machineconfig \
  --patch '[{"op": "replace", "path": "/machine/registries/mirrors/docker.io/endpoints/0",
             "value": "http://artefacts.podzone.cloud/v2/proxy-dockerhub"}]'
```

Repeat for each cluster and each mirror. This is a rolling operation and requires each
node to apply and reboot with the new config.

**Decision needed (Team Lead)**: Should existing clusters be patched before Harbor is
powered off, or will Harbor stay running until clusters are next reprovisioned? Current
guidance in PROJ-010/T-004 says Harbor VM is kept powered until Nexus is confirmed
working — so existing clusters can continue to use Harbor until the VM is explicitly
scheduled for decommission.

### Step 6 — Update test data ✅ Complete 2026-03-26

Update the gitopsapi-create test fixture:

File: `/Users/martincolley/workspace/code/gitopsapi/tests/test_data/applications/gitopsapi-create.json`
Line 10: change `harbor.podzone.cloud/gitopsapi/gitopsapi` → `ghcr.io/motttt/gitopsapi`

### Step 7 — Add Nexus port forward to freyr (if needed)

If `artefacts.podzone.cloud` routes via Cloudflare to freyr, and the Nexus cluster LB IP
needs to be exposed, add a forward to `freyr-port-forwards.yaml` analogous to the existing
`gitopsapi` forward (port 8081 → openclaw Gateway 192.168.4.179:80).

Once Nexus is confirmed working and all clusters are re-pointed:

### Step 8 — Remove Harbor port forward

Remove the `harbor-http` entry from
`/Users/martincolley/workspace/code/podzone-infrastructure/config/freyr-port-forwards.yaml`
and apply via ansible-playbook.

---

## Section 5 — What This Unblocks

**PROJ-010/T-004 — Harbor VM decommission** is explicitly blocked on:

1. Nexus configured and operational as a pull-through proxy (this task)
2. ExtraManifests URL audit complete — confirmed: the `extraManifests` URLs in active
   clusters (`http://192.168.4.1/...`) serve bootstrap manifests (cilium, flux, metrics-server,
   piraeus-operator, kubelet-serving-cert-approver). These are sourced from the freyr/bastion
   host (192.168.4.1), not from Harbor. The images referenced in those manifests pull from
   `ghcr.io`, `registry.k8s.io`, and `quay.io` — all upstream public registries. No
   extraManifest pulls from `harbor.podzone.cloud` or `192.168.4.100`.
3. All running cluster mirrors re-pointed to Nexus (or clusters confirmed tolerant of
   direct-pull fallback during the cutover window)

Once Nexus is live, tested, and mirrors are updated in the cluster-chart, the Harbor VM
(`192.168.4.100`) can be powered off and eventually deleted.

---

## Appendix — Image Registries Used by Current Workloads

For reference, here is the complete inventory of upstream registries used by deployed
workloads across all active cluster-repos:

| Registry | Images | Workloads |
|---|---|---|
| `ghcr.io` | `motttt/gitopsapi`, `fluxcd/*`, `alex1989hu/kubelet-serving-cert-approver`, `openclaw/openclaw`, `controlplaneio-fluxcd/flux-operator` | gitopsapi, Flux controllers, cert approver, OpenClaw |
| `quay.io` | `piraeusdatastore/*`, `cilium/*` | Piraeus (DRBD/Linstor), Cilium CNI |
| `registry.k8s.io` | `metrics-server/metrics-server`, `sig-storage/*` | Metrics server, CSI snapshotter |
| `docker.io` | `chromedp/headless-shell` | OpenClaw chromium sidecar |
| `qdrant/qdrant` | (Docker Hub) | Qdrant vector DB (via Helm chart default) |
| Helm charts | otwld.com, qdrant.github.io, sonatype, helm.goharbor.io | Nexus, Ollama, Qdrant charts — these are OCI/HTTP Helm repos, not image registries |

Note: `qdrant/qdrant` and `ollama/ollama` (Docker Hub) are pulled via Helm chart defaults —
the values files in openclaw-apps and (planned) agentsonly-apps do not override the
`image.repository`, so these pull from Docker Hub directly. Routing them through the Nexus
Docker Hub proxy will require no manifest changes on the app side once the Talos mirror
config is in place (mirrors are transparent to the workload).
