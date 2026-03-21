# Questions / Task Breakdowns for Team Lead

**Purpose**: Claude Code writes questions, task breakdowns, and blockers here. The Team Lead monitors this file and responds via team-tasklist.md updates or direct instructions.

---

## Template

```markdown
## [TASK-XXX] Task Name

**Status**: Blocked / Clarification needed / Breakdown proposed / Question

**Question/Breakdown**:
- Item 1
- Item 2

**Context**: Why this matters / what you've tried / what you need
```

---

## ⚠️ CRITICAL: Shared Context Usage (2026-03-11)

### YOU ARE "CODER" ROLE — USE QDRANT FOR CONTEXT, NOT LOCAL FILES

**Rule:** Query Qdrant (<http://localhost:6333>) for specifications, requirements, prior decisions.

**DO NOT** read files from `/Users/martincolley/workspace/podzoneAgentTeam/` or `cluster09/` for context.

**If context missing/stale:** Raise task to Team Lead (add entry to this file or create `podzoneAgentTeam/agents/team-lead/incoming/YYYY-MM-DD-{task}.md`).

**Only read files directly when:** Writing/editing that specific file. **NEVER** for context retrieval.

**See:** `READMEFIRST.md` (updated with full pattern), `podzoneAgentTeam/planning/ROLE-PLAYER-MAPPING.md`

---

## Active Questions

## [PROJ-007/T-010] openclaw decommission — PRs raised, action needed (2026-03-21)

**Status**: Two PRs raised — merge in order

**Work done this session**:

- Identified that the prior decommission (cluster09 PR #17, 2026-03-18) removed files from the wrong repo
- The active `openclaw-cluster` Kustomization is managed from `management-infra` and reads from `cluster-charts`
- Raised correct PRs:
  1. `MoTTTT/management-infra` PR #6 — removes `openclaw-cluster` from `clusters/management/clusters.yaml`
  2. `MoTTTT/cluster-charts` PR #5 — removes `gitops/cluster-charts/openclaw/` files
- `openclaw-infra` + `openclaw-apps` already archived ✅

**Merge order (action required)**:

1. Merge **management-infra PR #6** first → Flux prunes `openclaw-cluster` Kustomization → CAPMOX deletes VMs
2. Verify VMs gone on venus Proxmox
3. Merge **cluster-charts PR #5** → clean up chart files

**Additional cleanup needed after merge**:

- Remove iptables PREROUTING rule on freyr: `iptables -t nat -D PREROUTING -p tcp --dport 6447 -j DNAT --to-destination 192.168.4.150:6443`
- Update DNS: `qdrant.podzone.cloud` + `ollama.podzone.cloud` → agentsonly Gateway IP (currently unreachable at 192.168.1.80:6448)
- Fix agentsonly iptables: port 6448 should map to agentsonly (192.168.4.160:6443)

**Known gap — ClusterService uses wrong repo**:

The `ClusterService.decommission_cluster()` writes to `GITOPS_REPO_URL` (management infra repo) only.
Cluster-chart files live in `MoTTTT/cluster-charts` (a separate repo). The service needs to be updated
to write cluster-chart files to `cluster-charts` and Kustomization entries to management-infra.
This gap was masked because the decommission PR #17 wrote to `cluster09` (another wrong repo).
Flag as a bug: ClusterService needs a second git/gh client for the cluster-charts repo.

---

## [CC-079] gitopsdev kubeconfig + API status (2026-03-20)

**Status**: ✅ Resolved — kubeconfig refreshed, API healthy

**Resolution**:

- gitopsdev was rebuilt 2026-03-19; new CA/certs issued; CAPI kubeconfig secret not updated automatically
- Fix: extracted new CA from `gitopsdev-ca` CAPI secret, replaced gitopsdev entry in `~/.kube/config`
- `gitopsapi` running: chart 0.1.3, image `ghcr.io/motttt/gitopsapi:v0.1.6`, pod Ready
- Live API confirmed via port-forward: auth check passes, `GET /api/v1/clusters` returns `[]` (expected — CC-055 gap)

**Remaining blocker for live HTTP access (freyr:8081)**:

Gateway API not yet deployed to gitopsdev — PROJ-007/T-002 (Cluster Operator action required).
Once deployed, the existing HTTPRoute (`gitopsgui.podzone.cloud`) will attach and the API will be reachable.

**New gap identified — PROJ-003 backlog**:

`GET /api/v1/clusters/{name}/kubeconfig` endpoint needed. After cluster provisioning, operators must
manually extract the kubeconfig from the CAPI secret on the management cluster. An API endpoint would:

1. Pull `{cluster}-kubeconfig` secret from management cluster
2. Rewrite `server:` to the bastion address (`https://freyr:{bastion_port}`)
3. Return the kubeconfig for download

**Action required**:

Refresh the kubeconfig for gitopsdev:

```bash
talosctl kubeconfig --nodes 192.168.4.120 --endpoints 192.168.4.120 --force -n gitopsdev
```

(Or re-export from Talos and update `~/.kube/config`.)

---

## [CC-078] Deployment status — manual action required (2026-03-19)

**Status**: Blocked on ghcr.io package visibility

**Work completed this session**:

- `git_service.py` + `repo_router.py`: HTTPS+PAT auth (SSH removed)
- Helm chart 0.1.3: ssh-key volume removed, image v0.1.6, published to GitHub Pages Helm repo
- `cluster09` + `gitopsdev-apps` updated: chart 0.1.3, image v0.1.6, HTTPS repoUrl, no sshKeySecret
- 4 secrets recreated on gitopsdev: `ghcr-pull-secret`, `gitopsapi-github-token`, `gitopsapi-mgmt-kubeconfig`, `gitopsapi-age-key`
- Image `ghcr.io/motttt/gitopsapi:v0.1.6` built and pushed to ghcr.io from erectus

**Blocker**: The PAT in `flux-secret.yaml` has `packages:write` but NOT `packages:read`. Kubernetes nodes cannot pull the private image. The `ghcr-pull-secret` auth fails on image pull.

**Action required** (cannot be done without packages scope):

Make `ghcr.io/motttt/gitopsapi` package **public**:

1. Go to <https://github.com/MoTTTT?tab=packages>
2. Click `gitopsapi` → Package Settings → Change visibility → **Public**

Once public, the HelmRelease will reconcile automatically (retrigger with `flux reconcile hr gitopsapi -n flux-system` on gitopsdev if needed).

---

## [PROJ-003/T-010+T-011] platform-services cluster — API-first test session complete (2026-03-18)

**Status**: Complete — all 11 API calls succeeded on local dev server; test data files written

**Summary of work done**:

- Added `PlatformSpec` model; changed `ClusterSpec.platform` from `str` to `Optional[PlatformSpec]`
- Added `ClusterSpec.external_hosts: List[str]` — FQDNs for Gateway listeners + cert-manager SANs
- Added `ApplicationClusterConfig.external_hosts: List[str]` — per-app subset, stored as Kustomization annotation `gitopsapi.podzone.net/external-hosts`
- All 228 tests passing

**API calls validated (local dev server)**:

| # | Endpoint | Object |
| --- | --- | --- |
| 1 | `POST /api/v1/clusters` | platform-services (VIP .180, port 6448, venus/Proxmox) |
| 2 | `POST /api/v1/applications` | cloudnative-pg (prereq for keycloak) |
| 3 | `POST /api/v1/applications` | nexus (sonatype, artefacts.podzone.cloud) |
| 4 | `POST /api/v1/applications` | forgejo (git forge, git.podzone.cloud) |
| 5 | `POST /api/v1/applications` | keycloak (codecentric keycloakx v7.1.9, login.podzone.cloud) |
| 6 | `POST /api/v1/applications` | cloudflared (cloudflare-tunnel-remote v0.1.2) |
| 7–11 | `POST /api/v1/application-configs` | All 5 apps assigned to platform-services |

**Test data files created**:

- `tests/test_data/clusters/platform-services-create.json`
- `tests/test_data/applications/{cloudnative-pg,nexus,forgejo,keycloak,cloudflared}.json`
- `tests/test_data/application-configs/{cloudnative-pg,nexus,forgejo,keycloak,cloudflared}-platform-services.json`

**Known issue — live API (freyr:8081) returning 404**:

Envoy gateway reachable but all GitOpsAPI paths return 404. Root cause: openclaw cluster (where gitopsapi runs) is being decommissioned. All testing done on local dev server.

**Action needed from Team Lead**:

1. Build and deploy `v0.1.6` image with PlatformSpec + external_hosts changes
   - Image build host: erectus (192.168.1.201), see CLAUDE.md for rsync + build commands
   - Deploy to gitopsdev cluster (HelmRelease `gitopsapi/gitopsapi-gitopsapi`)
2. Confirm platform-services is the right cluster for Nexus + Forgejo (see [ROADMAP] item below re management cluster)
3. Live iptables on freyr: agentsonly port needs correcting from 6448 → 6446 (platform-services takes 6448 by formula `6430 + block_number`)

---

## [FR] API-First Testing Protocol (2026-03-17)

**Status**: Feedback from testing session — action needed in test plan + CLAUDE.md

**Observation**: When provisioning new clusters or deploying applications via the API, we should:

1. Define each object's full attribute set (all fields, types, defaults, constraints, rules) in writing
2. Review the definition before making any API call
3. This catches schema gaps and misconfiguration earlier — before roundtrip failures in Flux or CAPI

**Action requested**:

- Add a formal "pre-call attribute review" step to the E2E test plan for `/clusters` and `/applications`
- Add working practice note to CLAUDE.md: "Before calling any GitOpsAPI write endpoint in testing, document and review all object attributes"

---

## [FR] Hypervisor support in ClusterSpec (2026-03-17)

**Status**: Feature request — schema + ETE environment

**Background**: Currently `ClusterSpec` has no hypervisor field. All clusters implicitly target `venus` (192.168.4.50). The ETE test environment only has one hypervisor.

**Requests**:

1. **Schema** (PROJ-003/T-010): Add `hypervisor` field to `ClusterSpec`; define all hypervisor object attributes (name, Proxmox URL, node IPs, credentials ref, capacity) before implementation
2. **ETE environment** (PROJ-001/T-005): Add a second hypervisor so cluster creation can be tested with explicit hypervisor assignment
3. **Test plan**: Add test case for `POST /clusters` with hypervisor specified; verify CAPI `ProxmoxCluster` targets the correct host

---

## [ROADMAP] Management cluster — versioned replacement strategy (2026-03-17)

**Status**: Architecture decisions captured — PROJ-001 updated

**Decisions**:

- **Naming convention**: version is encoded in cluster name — `management00`, `management01`, etc.
- **Why versioning**: CAPI cannot be upgraded in-place on a self-managed cluster. Upgrading Talos version, Kubernetes version, or CAPI version requires: spin up new cluster → migrate workloads and CAPI control → shut down old cluster.
- **CAPI Operator** (PROJ-001/T-006): Moving from `clusterctl init` CLI to [CAPI Operator](https://github.com/kubernetes-sigs/cluster-api-operator). CAPI providers are defined as `CAPIProvider` / `InfrastructureProvider` CRs, committed to git, reconciled by Flux — no manual `kubectl` apply.
- **Shared workloads on management**: Management cluster will host shared platform services including Nexus and Forgejo (in-house git forge).

**Open question for Team Lead** (PROJ-001/T-007):

Stateful workloads (Nexus, Forgejo) on the management cluster add significant migration complexity during a versioned upgrade:

- Container registry data (Nexus) and git repos (Forgejo) must be migrated to the new cluster before cutover
- CloudNativePG databases (if used) require backup/restore or replication to new cluster
- Downtime window needed unless active-passive replication is set up

**Options**:

1. Keep Nexus + Forgejo on management cluster — accept the migration cost; document a migration runbook
2. Place them on a dedicated `platform-services` cluster — management upgrade becomes stateless, much simpler
3. Hybrid — Forgejo on management (git is the source of truth, already replicated), Nexus on separate cluster (registry data is large and hard to migrate)

**Decision needed before** PROJ-001/T-001 (management00 provisioning) and PROJ-006/T-002 (Forgejo deployment).

---

## [TASK-055] Archive Completed Tasks

**Status**: New (delegated from Trismagistus 2026-03-11 00:12 GMT)

**Action needed**:

1. Read `podzoneAgentTeam/planning/tasks.md` (old format)
2. Identify all ✅ Completed tasks
3. Move to `podzoneAgentTeam/planning/completed-tasks.md` under 2026-03-11 section
4. Format: Brief (task ID, completed date, agent, outcome only)

**Detail**: `podzoneAgentTeam/planning/INTER-AGENT-MESSAGING.md`

**Context**: Part of inter-agent messaging refactor. Clean context by archiving completed work.

---

## [TASK-056] Update Agent Personas with New Messaging Protocol

**Status**: New (delegated from Trismagistus 2026-03-11 00:12 GMT)

**Action needed**:

1. Update `podzoneAgentTeam/agents/claude-code/AGENT.md` (create if missing)
2. Update `gitopsapi/READMEFIRST.md` to reference new system:
   - Write tasks to `podzoneAgentTeam/agents/claude-code/trismagistus-tasks.md`
   - Read team tasks from `podzoneAgentTeam/planning/team-tasklist.md`
   - Detail files in `podzoneAgentTeam/agents/claude-code/details/`
3. Update `podzoneAgentTeam/agents/trismagistus/AGENT.md` if needed
4. Update `podzoneAgentTeam/agents/claude-web/AGENT.md` (create if missing)

**Detail**: `podzoneAgentTeam/planning/INTER-AGENT-MESSAGING.md`

**Context**: Document new inter-agent messaging system in agent personas.

---

## [TASK-029] GitOpsAPI Helm Chart — Publish to GitHub (ghcr.io)

**Status**: Redirected — Harbor approach abandoned; publish as OCI artifact to ghcr.io (2026-03-16)

**Decision**: Push Helm chart as OCI artifact to `ghcr.io/motttt/charts/gitopsapi` instead of Harbor.

**Next steps**:

```bash
helm package charts/gitopsapi/
helm push gitopsapi-0.1.0.tgz oci://ghcr.io/motttt/charts
```

**HelmRepository** in cluster should reference `oci://ghcr.io/motttt/charts`.

**Prior Harbor work** (superseded):

- Harbor project `gitopsapi` was created (project_id: 9) but Harbor chartrepo API unreliable
- Documentation: `podzoneAgentTeam/infrastructure/harbor-docker-restart.md`

---

## Resolved

(Team Lead will move resolved items here with answers.)

<!-- End of file -->
