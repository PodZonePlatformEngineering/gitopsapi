# Application Cluster — To-Be State

**Authored:** 2026-03-16
**Based on:** [application-deployment-as-is.md](application-deployment-as-is.md)

---

## Summary of Changes

| Change | Detail |
| --- | --- |
| New cluster | `agentsonly` — AI/agent workloads, 1 CP + 1 worker |
| Decommission cluster | `openclaw` — workloads migrated to agentsonly |
| Migrate applications | qdrant, ollama, podzone-mpc, openclaw → agentsonly |
| Remove from gitopsdev | podzone-mpc |
| openclaw VM (Docker) | Decommission 192.168.4.101 ollama Docker VM once in-cluster migration confirmed |
| Decommission Harbor | Harbor VM (192.168.4.100) — decision 2026-03-17. Replaced by Nexus + GHCR |
| New: Nexus | Pull-through proxy for cluster image pulls (HIGH priority, replaces Harbor role) |
| Ollama auth | Add API key authentication to Ollama HelmRelease values (decision 2026-03-17) |
| Decommission bladon.podzone.cloud | Home Assistant (192.168.1.201) turning down |
| Migrate agent.podzone.cloud | Ollama WebUI → agentsonly cluster once Ollama migration complete |

---

## Target Cluster Inventory

| Cluster | API via freyr | Internal VIP | Role | Status |
| --- | --- | --- | --- | --- |
| Management | 192.168.1.80:6450 | 192.168.4.211:6443 | CAPI management plane | No change |
| agentsonly | 192.168.1.80:6448 ⚠️ | 192.168.4.160:6443 | AI/agent cluster | **New** |
| gitopsdev | 192.168.1.80:6442 | 192.168.4.120:6443 | Dev pipeline + GitOpsAPI | No change |
| gitopsete | 192.168.1.80:6443 | 192.168.4.130:6443 | ETE pipeline | No change |
| gitopsprod | 192.168.1.80:6444 | 192.168.4.140:6443 | Prod pipeline | No change |
| openclaw | 192.168.1.80:6447 | 192.168.4.170:6443 | Decommission | **Remove** |

> ⚠️ Port 6448 is proposed for agentsonly freyr port-forward — confirm and add iptables rule.

### agentsonly — IP Allocation

Block: `192.168.4.160-169` (10 IPs — currently unallocated)

| IP | Assignment |
| --- | --- |
| 192.168.4.160 | Control-plane VIP (API endpoint) |
| 192.168.4.161 | Control-plane node |
| 192.168.4.162 | Worker node |
| 192.168.4.163–168 | LB pool (cilium LoadBalancerIPPool) |
| 192.168.4.169 | Reserved |

---

## Target Application Deployment Matrix

| Application | Category | Management | agentsonly | gitopsdev | gitopsete | gitopsprod | VM / External | Migration |
| --- | --- | :---: | :---: | :---: | :---: | :---: | :---: | --- |
| gitopsapi | Platform Mgmt | | | ✅ | | | | No change |
| gitopsgui | Platform Mgmt | | | | | | | Planned — gitopsdev first |
| keycloak | Security | | | | | | | Not deployed; migration TBD |
| oauth2-proxy | Security | | | | | | | Pending Keycloak |
| harbor | Artifact Mgmt | | | | | | 🖥️ 192.168.4.100 | **Decommission** — decision 2026-03-17. Replaced by Nexus + GHCR |
| nexus | Artifact Mgmt | | ✅ | | | | | **New — HIGH priority.** Pull-through proxy for cluster image pulls |
| qdrant | AI/ML | | ✅ | | | | | Migrate: openclaw → agentsonly |
| ollama | AI/ML | | ✅ | | | | | Migrate: openclaw → agentsonly; decommission VM (192.168.4.101); add API key auth (Option B, 2026-03-17) |
| opensearch stack | Observability | | | | | | | Not deployed; migration TBD |
| opensearch-operator | Observability | | | | | | | Not deployed; migration TBD |
| prometheus / grafana | Observability | | | | | | | Not deployed; migration TBD |
| fluent-bit | Observability | | | | | | | Not deployed; migration TBD |
| openclaw | Networking | | ✅ | | | | | Migrate: openclaw cluster → agentsonly; acts as telegram bot placeholder |
| wso2 | API Mgmt | | | | | | | Not deployed; review for decommission |
| cert-manager | Platform Infra | ✅ | ✅ | ✅ | | | | Add: agentsonly |
| piraeus affinity-ctrl | Platform Infra | ⚙️ | ✅ | ✅ | | | | Add: agentsonly |
| podzone-mpc | AI/ML Infra | | ✅ | | | | | Migrate: gitopsdev → agentsonly |
| docker-build-agent | CI/CD | | | | | | 🖥️ erectus | No change |
| forgejo | Source Control | | | | | | | Planned (TASK-039) |
| cloudnativepg | Data | | | | | | | Planned (TASK-041) |

---

## Migration Plan

### Phase 1 — Provision agentsonly cluster

1. Add `agentsonly` cluster-chart HelmRelease on Management cluster
   - 1 control-plane node, 1 worker node
   - IP block: 192.168.4.160-169, VIP: 192.168.4.160
   - `extra_manifests`: cilium, flux, `flux-instance-agentsonly.yaml`, flux-secret, gateway-api
2. Add `flux-instance-agentsonly.yaml` to local HTTP server (192.168.4.1)
3. Add `clusters/agentsonly/` to cluster09 gitops repo
4. Add freyr iptables rule: port 6448 → 192.168.4.160:6443
5. Add agentsonly context to local kubeconfig

### Phase 2 — Deploy applications to agentsonly

1. Add HelmReleases to agentsonly gitops path:
   - cert-manager
   - piraeus affinity-controller
   - qdrant
   - ollama
   - openclaw (namespace: `openclaw`, as telegram bot placeholder)
   - podzone-mpc (with updated Qdrant + Ollama service endpoints pointing to agentsonly)
2. Verify all HelmReleases reach Ready=True

### Phase 3 — Migrate and decommission openclaw cluster

1. Confirm qdrant data migration (snapshot → restore to agentsonly qdrant)
2. Update podzone-mpc on gitopsdev to point to agentsonly qdrant/ollama endpoints (interim)
3. Remove podzone-mpc HelmRelease from gitopsdev
4. Verify podzone-mpc on agentsonly is operational
5. Remove openclaw HelmRelease from Management cluster (triggers CAPI cluster deletion)
6. Release IP block 192.168.4.170-179 in IP inventory
7. Remove openclaw iptables rule (freyr port 6447) — or repurpose
8. Remove openclaw context from kubeconfig

### Phase 4 — Decommission openclaw Docker VM (ollama)

1. Confirm ollama on agentsonly is serving all consumers (podzone-mpc, Claude Code MCP)
2. Update any external references from 192.168.4.101:11434 to agentsonly ollama service
3. Power down VM 120 (192.168.4.101)
4. Update IP inventory

---

## Notes

### openclaw app as telegram bot placeholder

The `openclaw` HelmRelease (`openclaw-helm` chart) is deployed on agentsonly as a placeholder in the `openclaw` namespace. This namespace will be repurposed for the telegram bot application (TASK: telegram-bot) once developed. The openclaw reverse proxy is not used for traffic in this configuration — it provides a named namespace and deployment target.

### qdrant data migration

qdrant on openclaw holds vector embeddings for podzone-mpc (semantic context store). Before decommissioning openclaw, a qdrant snapshot must be taken and restored to agentsonly. Alternatively, podzone-mpc can re-seed its context store from scratch if acceptable.

### Cloudflare Tunnel (Bladon) routing

If any Cloudflare Tunnel routes currently target openclaw Gateway (192.168.4.178/179), these must be migrated to the agentsonly Gateway before openclaw is decommissioned.
