# Application Deployment — As-Is

**Audited:** 2026-03-16
**Method:** Live `kubectl` audit across all active clusters via freyr (192.168.1.80).
**Source catalog:** [application-catalog.md](application-catalog.md)

---

## Cluster Inventory

| Cluster | API via freyr | Internal API | Status |
| --- | --- | --- | --- |
| Management | 192.168.1.80:6450 | 192.168.4.211:6443 | Active — CAPI management plane |
| openclaw | 192.168.1.80:6447 | 192.168.4.170:6443 | Active — primary application cluster |
| gitopsdev | 192.168.1.80:6442 | 192.168.4.120:6443 | Active — dev pipeline + GitOpsAPI host |
| gitopsete | 192.168.1.80:6443 | 192.168.4.130:6443 | Active — bare (Flux only, no Helm controller) |
| gitopsprod | 192.168.1.80:6444 | 192.168.4.140:6443 | Active — bare (Flux only, no Helm controller) |

**VM / External hosts:**

| Host | IP | Role |
| --- | --- | --- |
| harbor | 192.168.4.100 | Docker VM — container registry |
| ollama (VM) | 192.168.4.101 | Docker VM — LLM inference (parallel to in-cluster) |
| erectus | 192.168.1.201 | Docker host — build agent |

---

## Deployment Matrix

**Legend:**

| Symbol | Meaning |
| --- | --- |
| ✅ | Live — HelmRelease Ready=True (or equivalent) |
| ⚙️ | Platform component — running via cluster bootstrap (not app HelmRelease) |
| ⚠️ | Configured but inactive — HelmRelease commented out or paused |
| ❌ | Not deployed — catalog claimed deployment; live state shows absent |
| 🖥️ | VM / External — not running in a Kubernetes cluster |
| 🏗️ | Planned |
| 💡 | Proposed |

| Application | Category | Management | openclaw | gitopsdev | gitopsete | gitopsprod | VM / External | Notes |
| --- | --- | :---: | :---: | :---: | :---: | :---: | :---: | --- |
| gitopsapi | Platform Mgmt | | | ✅ | | | | HelmRelease True (v0.1.2) |
| gitopsgui | Platform Mgmt | | | | | | | 🏗️ Planned — gitopsdev first |
| keycloak | Security | | | | | | | ❌ Was on cluster09 (decommissioned); not migrated |
| oauth2-proxy | Security | | | | | | | ⚠️ HelmRepo defined; Release not applied — pending Keycloak |
| harbor | Artifact Mgmt | | | | | | 🖥️ 192.168.4.100 | Docker VM only; not in-cluster |
| qdrant | AI/ML | | ✅ | | | | | StatefulSet, HelmRelease True |
| ollama | AI/ML | | ✅ | | | | 🖥️ 192.168.4.101 | In-cluster HelmRelease True + parallel Docker VM |
| opensearch stack | Observability | | | | | | | ❌ Was on cluster09; not migrated (4 HelmReleases) |
| opensearch-operator | Observability | | | | | | | ❌ Was on cluster09; not migrated |
| prometheus / grafana | Observability | | | | | | | ❌ Was on cluster09; not migrated |
| fluent-bit | Observability | | | | | | | ❌ Was on cluster09; not migrated |
| openclaw | Networking | | ✅ | | | | | HelmRelease True (openclaw-helm v1.3.22) |
| wso2 | API Mgmt | | | | | | | ❌ Was on cluster09; not migrated |
| cert-manager | Platform Infra | ✅ | ✅ | ✅ | | | | HelmRelease True on all three active app clusters |
| piraeus affinity-controller | Platform Infra | ⚙️ | ✅ | ✅ | | | | Management: linstor via bootstrap; openclaw+gitopsdev: HelmRelease True |
| cluster09-docs | Documentation | | | | | | | ❌ Was on cluster09; not migrated |
| podzone-docs | Documentation | | | | | | | ❌ Was on cluster09; not migrated |
| podzone-mpc | AI/ML Infra | | | ✅ | | | | HelmRelease True — migrated from erectus Docker compose |
| docker-build-agent | CI/CD | | | | | | 🖥️ erectus | Manual rsync + docker build workflow |
| forgejo | Source Control | | | | | | | 🏗️ TASK-039 — gitopsdev first |
| cloudnativepg | Data | | | | | | | 🏗️ TASK-041 |
| nexus | Artifact Mgmt | | | | | | | 🏗️ Planned (HIGH) — replaces Harbor as pull-through proxy; own images → GHCR |
| redis | Messaging | | | | | | | 💡 Proposed |
| nfs-server | Storage | | | | | | | 💡 Proposed |
| everything-ai | AI/ML | | | | | | | 💡 Proposed |
| telegram-bot | Platform Mgmt | | | | | | | 💡 Strategic |

---

## Observations

### cluster09 decommission gap

The following applications were listed in the catalog as deployed on `cluster09`, which is now decommissioned. None have been migrated to an active cluster:

- **keycloak** — security/identity; blocks oauth2-proxy and downstream auth
- **opensearch stack** (os-master, os-data, os-client, opensearch-dashboards) — observability logging
- **opensearch-operator** — prerequisite for opensearch
- **prometheus / grafana** (`kube-prometheus-stack`) — metrics and dashboards
- **fluent-bit** — log forwarder to opensearch
- **wso2** — API manager; review pending for decommission vs migration
- **cluster09-docs / podzone-docs** — static docs sites

### gitopsete / gitopsprod — bare clusters

Both pipeline clusters are provisioned but have only `flux-operator`, Cilium CNI, and `talos-cloud-controller-manager`. The Flux Helm controller CRD is not installed — `HelmRelease` resources cannot be applied. No application workloads are present.

### ollama — dual deployment

Ollama runs both as an in-cluster HelmRelease on openclaw **and** as a Docker container on the VM at 192.168.4.101. These are independent instances; reconciliation/deduplication is an open question.

### Management cluster role

Management hosts CAPI controllers (cabpt, cacppt, capi, capmox, capi-ipam), Flux, piraeus/linstor, and the cluster-chart HelmReleases that provision gitopsdev/ete/prod/openclaw. It is an infrastructure management plane, not an application host.

---

## HelmRelease Summary by Cluster

### Management

| Namespace | HelmRelease | Chart | Ready |
| --- | --- | --- | --- |
| cert-manager | cert-manager | cert-manager | ✅ |
| gitopsdev | gitopsdev | cluster-chart | ✅ |
| gitopsete | gitopsete | cluster-chart | ✅ |
| gitopsprod | gitopsprod | cluster-chart | ✅ |
| openclaw | openclaw | cluster-chart | ✅ |

### openclaw

| Namespace | HelmRelease | Chart | Ready |
| --- | --- | --- | --- |
| affinity-controller | affinity-controller | linstor-affinity-controller | ✅ |
| cert-manager | cert-manager | cert-manager | ✅ |
| flux-system | ollama | ollama | ✅ |
| flux-system | openclaw | openclaw-helm | ✅ |
| flux-system | qdrant | qdrant | ✅ |

### gitopsdev

| Namespace | HelmRelease | Chart | Ready |
| --- | --- | --- | --- |
| affinity-controller | affinity-controller | linstor-affinity-controller | ✅ |
| cert-manager | cert-manager | cert-manager | ✅ |
| flux-system | gitopsapi | gitopsapi | ✅ |
| flux-system | podzone-mpc | podzone-mpc | ✅ |

### gitopsete

No HelmRelease CRD installed. No application workloads.

### gitopsprod

No HelmRelease CRD installed. No application workloads.
