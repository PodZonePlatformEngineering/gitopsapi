# QUESTIONS Archive

Resolved, actioned, or superseded items moved from QUESTIONS.md.

---

## 2026-03-22

### [CC-079] gitopsdev kubeconfig + API status

**Status**: ✅ Resolved

- gitopsdev rebuilt 2026-03-19; new CA/certs issued; CAPI kubeconfig secret not updated automatically
- Fix: extracted new CA from `gitopsdev-ca` CAPI secret, replaced gitopsdev entry in `~/.kube/config`
- `gitopsapi` running: chart 0.1.3, image `ghcr.io/motttt/gitopsapi:v0.1.6`, pod Ready
- Live API confirmed via port-forward: auth check passes, `GET /api/v1/clusters` returns `[]` (CC-055 gap, expected)
- Gateway API gap remains: HTTPRoute won't attach until Gateway deployed to gitopsdev (CC-066)
- New gap logged to PROJ-003 backlog: `GET /api/v1/clusters/{name}/kubeconfig` endpoint

---

### [CC-078] Deployment status

**Status**: ✅ Resolved (2026-03-22)

- SSH auth replaced with HTTPS+PAT in git_service.py
- Helm chart 0.1.3 published; image v0.1.6 built and pushed to ghcr.io
- ghcr.io/motttt/gitopsapi package made public; image pull succeeds
- cluster09 + gitopsdev-apps updated to chart 0.1.3, image v0.1.6
- 4 secrets recreated on gitopsdev

---

### [PROJ-003/T-010+T-011] platform-services cluster — API-first test session

**Status**: ✅ Complete (2026-03-18)

- PlatformSpec model added; ClusterSpec.platform changed from str to Optional[PlatformSpec]
- ClusterSpec.external_hosts: List[str] added; ApplicationClusterConfig.external_hosts added
- All 11 API calls validated on local dev server
- Test data files written for: platform-services cluster, cloudnative-pg, nexus, forgejo, keycloak, cloudflared, and all 5 app-configs

---

### [FR] API-First Testing Protocol

**Status**: ✅ Actioned — added to CLAUDE.md and docs/api-first-testing-protocol.md (2026-03-17)

Pre-call attribute review step is now required practice before every POST/PUT in a test session.

---

### [FR] Hypervisor support in ClusterSpec

**Status**: ✅ Documented — feature request captured in PROJ-001 backlog

- `hypervisor` field to be added to ClusterSpec (full attribute definition first)
- Second hypervisor for ETE: saturn (192.168.4.51) defined as PROJ-001/T-005 (complete 2026-03-22)
- Test case to be added when schema change is implemented

---

### [ROADMAP] Management cluster — versioned replacement strategy

**Status**: ✅ Decisions captured — PROJ-001 updated (2026-03-17)

- Naming: `management00`, `management01`, ...
- CAPI Operator replaces `clusterctl init` CLI (PROJ-001/T-006)
- Shared workloads (Nexus, Forgejo): decided → platform-services cluster (not management)

---

## 2026-03-11

### [TASK-055] Archive Completed Tasks

**Status**: ✅ Actioned — completed tasks moved to completed-tasks.md

---

### [TASK-056] Update Agent Personas with New Messaging Protocol

**Status**: ✅ Actioned — AGENT.md files and READMEFIRST.md updated for new inter-agent messaging system

---

### [TASK-029] GitOpsAPI Helm Chart — Publish to GitHub

**Status**: ✅ Redirected and completed — chart published as GitHub Pages Helm repo (not OCI/ghcr.io)

- Helm chart 0.1.3 at https://motttt.github.io/cluster09/
- HelmRepository in gitopsdev-apps references this URL

---
