# Questions / Task Breakdowns for Team Lead

**Purpose**: Claude Code writes questions, task breakdowns, and blockers here. The Team Lead monitors this file and responds via team-tasklist.md updates or direct instructions.

Resolved items are archived in `QUESTIONS-ARCHIVE.md`.

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

## [CC-079] Cloudflare tunnel token — action needed to complete SOPS persistence (2026-03-22)

**Status**: Partially complete — scaffolding done, token encryption blocked on token value

**Work done**:

- Added `decryption` block to `cloudflared` Kustomization in `management-infra/clusters/management/infrastructure.yaml`
- Created skeleton `cloudflare-tunnel-token.sops.yaml` in `management-infra/gitops/gitops-management/02-network/`
- management-infra PR #7 open: `feat/cc-079-cloudflare-tunnel-token-sops`

**Action required from Martin** (1 command on freyr or local kubectl):

```bash
# 1. Get the live token from the cluster
kubectl --context management-admin@management \
  get secret cloudflare-tunnel-token -n cloudflared \
  -o jsonpath='{.data.token}' | base64 -d

# 2. Replace the placeholder in the file:
#    management-infra/gitops/gitops-management/02-network/cloudflare-tunnel-token.sops.yaml
#    token: REPLACE_WITH_ACTUAL_TOKEN_THEN_RUN_SOPS_ENCRYPT → (paste actual token)

# 3. Encrypt the file (management-infra sops-keys/management.agekey required):
cd management-infra
SOPS_AGE_KEY_FILE=... sops --encrypt --in-place \
  gitops/gitops-management/02-network/cloudflare-tunnel-token.sops.yaml

# 4. Also store in secretctl vault for future sessions:
secretctl set cloudflare-tunnel-token
```

**After encryption**: commit + push + merge management-infra PR #7. Flux will reconcile and create the Secret from SOPS; the HelmRelease already references it.

---

## [PROJ-007/T-010] openclaw decommission — PRs raised, action needed (2026-03-21)

**Status**: Two PRs raised — merge in order

- `MoTTTT/management-infra` PR #6 — removes `openclaw-cluster` from `clusters/management/clusters.yaml`
- `MoTTTT/cluster-charts` PR #5 — removes `gitops/cluster-charts/openclaw/` files
- `openclaw-infra` + `openclaw-apps` already archived ✅

**Merge order (action required)**:

1. Merge **management-infra PR #6** first → Flux prunes `openclaw-cluster` Kustomization → CAPMOX deletes VMs
2. Verify VMs gone on venus Proxmox
3. Merge **cluster-charts PR #5** → clean up chart files

**Additional cleanup needed after merge**:

- Remove iptables PREROUTING rule on freyr: `iptables -t nat -D PREROUTING -p tcp --dport 6447 -j DNAT --to-destination 192.168.4.150:6443`
- Update DNS: `qdrant.podzone.cloud` + `ollama.podzone.cloud` → agentsonly Gateway IP
- Fix agentsonly iptables: port 6448 should map to agentsonly (192.168.4.160:6443)

**Known gap — ClusterService uses wrong repo**:

`ClusterService.decommission_cluster()` writes to `GITOPS_REPO_URL` (management infra repo) only.
Cluster-chart files live in `MoTTTT/cluster-charts` (a separate repo). Bug: ClusterService needs a
second git/gh client for the cluster-charts repo.

---

## [CC-080] agentsonly sops-age prerequisite (2026-03-22)

**Status**: Blocked on operational step

The `sops-age` Secret must exist in `flux-system` on the agentsonly cluster before Flux can decrypt
the `ollama-api-key.sops.yaml` Secret in the CC-080 PR.

**Action required**:

```bash
# Extract agentsonly age key from management-infra (requires management SOPS key)
SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt \
  sops -d management-infra/sops-keys/agentsonly.agekey.enc > /tmp/agentsonly.agekey

# Apply to agentsonly cluster
kubectl --context agentsonly-admin@agentsonly --server=https://192.168.1.80:6446 \
  create secret generic sops-age \
  --namespace=flux-system \
  --from-file=age.agekey=/tmp/agentsonly.agekey

rm /tmp/agentsonly.agekey
```

**After**: merge agentsonly-apps PR #4 + agentsonly-infra PR #4. Flux will decrypt the OLLAMA_API_KEY secret.

Also store the generated key `Ffjyf-huTdQbuO0K5c6iyV1tPEhPdlHaW0rnfGN1F-A` in secretctl:

```bash
secretctl set ollama-api-key
```

---

## Resolved

Archived items are in `QUESTIONS-ARCHIVE.md`.

<!-- End of file -->
