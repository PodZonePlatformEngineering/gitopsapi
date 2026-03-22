# GitOpsAPI Security Posture Analysis

**Author:** Hephaestus (Claude Code)
**Date:** 2026-03-22
**Task:** CC-082
**Status:** Draft — for review before OpenBao integration design

---

## 1. Credential Inventory

The following table documents every credential GitOpsAPI collects or uses across its operational lifecycle.

| Credential | Type | When collected | How currently stored | Component | Lifecycle |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `GITHUB_TOKEN` | GitHub PAT | API startup | Env var → embedded in HTTPS URLs in-memory | git_service, github_service | Until rotation; no automated expiry |
| `GITOPS_SSH_KEY_PATH` | SSH private key (ed25519) | API startup | Mounted from K8s Secret at `/etc/gitops-ssh/id_rsa` | git_service | Container lifetime |
| `MGMT_KUBECONFIG_SECRET` | Kubeconfig YAML | API startup | Env var → written to `/tmp/mgmt-kubeconfig` (mode 0600) | main.py, kubeconfig_service | Container lifetime |
| `MANAGEMENT_SOPS_PUBLIC_KEY` | Age public key | API startup | Environment variable | sops_service | Container lifetime |
| Deploy key pair (private) | SSH ed25519 | Cluster provisioning | Generated in /tmp → K8s Secret `flux-{repo}-key` in `flux-system` | deploy_key_service | Until cluster decommission |
| Deploy key pair (public) | SSH ed25519 | Cluster provisioning | Uploaded to GitHub via API | deploy_key_service | Until revocation |
| SOPS age key (private) | Age secret key | Cluster provisioning | Generated in-memory → K8s Secret `sops-age` in `flux-system` | sops_service | Until cluster decommission |
| SOPS age key (encrypted) | Age-encrypted age key | Cluster provisioning | Committed to `management-infra/sops-keys/{cluster}.agekey.enc` | sops_service | Permanent (archive) |
| Per-cluster kubeconfig | Kubeconfig YAML | CAPI provisioning | K8s Secret `{cluster}-kubeconfig` on management cluster | kubeconfig_service | Until cluster deleted |
| `MCP_AUTH_TOKEN` | Bearer token | Optional at startup | Environment variable | mcp/context_server.py | Optional; container lifetime |
| OAuth2 user/groups | HTTP headers | Per request | In-flight only (X-Forwarded-User, X-Auth-Request-Groups) | api/auth.py | Per-request only |
| `GITOPSGUI_DEV_ROLE` | Role string | Dev only | Environment variable | api/auth.py | Dev-only bypass; must not be set in production |

---

## 2. Security Posture Analysis — Three Scenarios

### 2.1 Scenario 1: Vanilla Install

**Definition:** GitOpsAPI deployed with no external secrets management. All credentials stored as Kubernetes Secrets in the `gitopsapi` namespace, injected as environment variables.

#### Credential Storage Model

| Credential | Location |
|---|---|
| `GITHUB_TOKEN` | K8s Secret in `gitopsapi` namespace → env var |
| Management kubeconfig | K8s Secret in `gitopsapi` namespace → env var |
| SOPS management age key (public) | K8s Secret in `gitopsapi` namespace → env var |
| SSH deploy key (for management repo) | K8s Secret `gitopsapi-ssh-key` in `gitopsapi` namespace |
| Generated deploy keys | K8s Secrets in target cluster `flux-system` namespace |
| Generated SOPS keys | K8s Secret `sops-age` in target cluster `flux-system` namespace |

#### Kubernetes RBAC Protection

Without deliberate hardening, the default posture is:

- `gitopsapi` Service Account has access to Secrets in its own namespace
- Any cluster-admin or namespace-admin can read Secrets directly
- Etcd encryption at rest is not configured by default on Talos clusters unless explicitly enabled via `EncryptionConfig`

In practice: Secrets are stored as base64 in etcd, not encrypted at rest.

#### Attack Surface — Namespace Compromise

A namespace-level compromise grants an attacker:

1. **GITHUB_TOKEN** → full GitHub API access: repo CRUD, deploy key upload/revocation, PR manipulation
2. **Management kubeconfig** → full management cluster access: read all CAPI secrets, all per-cluster kubeconfigs, all SOPS keys
3. Via management kubeconfig → all per-cluster kubeconfigs → full access to all provisioned clusters
4. Via management kubeconfig → `sops-age` Secret in `flux-system` → management SOPS private key → decrypt all `*.agekey.enc` files in management-infra → all per-cluster secrets

**Blast radius: entire platform.** All clusters, all repos, all deployed applications.

#### Operator Experience

- Token rotation: update K8s Secret, restart pod — fully manual
- No visibility into token expiry without querying GitHub
- No audit trail of which credentials were accessed

#### Viability Assessment

| Context | Viable? | Notes |
|---|---|---|
| Developer/hobbyist (single-node, self-managed) | **Yes** | Acceptable risk; low-value target; K8s RBAC on small cluster is manageable |
| Small enterprise (on-prem, small team) | **Marginal** | Acceptable if etcd encryption is enabled and RBAC is enforced; grows riskier with team size |
| Enterprise (multi-tenant, compliance) | **No** | All secrets in one namespace, no audit trail, no rotation, no HSM; fails standard compliance frameworks |

---

### 2.2 Scenario 2: SOPS-Managed Secrets

**Definition:** GitOpsAPI generates a SOPS age key at first run, stored as a K8s Secret. All operational secrets committed to GitOps repos are encrypted with SOPS age encryption. This is the current intended direction for the PodZone platform.

#### Trust Boundary

The trust boundary is the **management age key pair**:

```text
management age private key
    ├─ stored in: management cluster → flux-system/sops-age → age.agekey
    ├─ used to: decrypt all *.agekey.enc files in management-infra/sops-keys/
    └─ those files contain: per-cluster SOPS private keys
           └─ per-cluster SOPS keys decrypt: all SOPS-encrypted secrets in {cluster}-infra repos
```

The management private age key is the **master credential** for the entire platform. Its compromise cascades to all clusters.

#### Git Repository Exposure Model

| Content committed to git | Encrypted? | Who can decrypt? |
|---|---|---|
| `management-infra/sops-keys/{cluster}.agekey.enc` | Yes (age, armored) | Anyone with management age private key |
| `{cluster}-infra/*.sops.yaml` | Yes (age) | Anyone with that cluster's age private key |
| Cluster values YAML (non-sensitive config) | No | Anyone with repo read access |
| HelmRelease values (non-sensitive) | No | Anyone with repo read access |

An attacker with only git access cannot decrypt SOPS-encrypted secrets without the age keys.

#### Key Loss Scenario

If the management age key Secret is deleted or the management cluster is destroyed without backup:

- All `*.agekey.enc` files in management-infra become **unrecoverable**
- Per-cluster `sops-age` Secrets remain usable while those clusters are live
- Re-bootstrapping any cluster requires generating a new SOPS key and re-encrypting all cluster secrets
- **No disaster recovery path exists without a backup of the management age key**

Current gap: the management age key has no documented backup or disaster recovery procedure.

#### Key Rotation Story

Rotating a cluster's SOPS key:

1. Generate new age key
2. Decrypt all encrypted secrets with old key
3. Re-encrypt all secrets with new key
4. Commit re-encrypted secrets to git
5. Update `sops-age` Secret in target cluster
6. Trigger Flux reconciliation

This is a complex, all-or-nothing operation (multiple files, must be atomic) and is currently entirely manual. Risk of partial rotation leaving inconsistent state.

#### Scenario 2 Viability Assessment

| Criterion | Assessment |
|---|---|
| Audit trail | Poor — no log of which secrets were decrypted or accessed |
| Key rotation | Supported but manual and complex |
| Per-tenant isolation | Partial — each cluster has its own age key, but management key is a single point of failure |
| Disaster recovery | Weak — dependent on a K8s Secret with no automated backup |
| Compliance | Marginal — secrets not readable in git, but master key lacks HSM-grade protection |

**Verdict:** Viable for a small, technically sophisticated team managing a fixed cluster set. Not viable for multi-tenant deployments where tenants must not share a common master key, or where compliance requires audit logging and key management auditability.

---

### 2.3 Scenario 3: External Keystore + SOPS

**Definition:** GitOpsAPI authenticates to an external secrets store (OpenBao on platform-services) to retrieve credentials at runtime. SOPS is retained for secrets committed to git, but the SOPS age key lives in OpenBao rather than a K8s Secret.

#### Authentication Model

GitOpsAPI authenticates to OpenBao via **Kubernetes Service Account JWT**:

```
gitopsapi Pod
    └─ ServiceAccount token (auto-mounted, signed by management K8s API server)
    └─ presented to OpenBao → kubernetes auth backend
    └─ OpenBao validates token against management cluster API
    └─ issues scoped Vault token for policy: gitopsapi
    └─ gitopsapi fetches credentials dynamically at startup / on demand
```

One-time operator setup: configure OpenBao Kubernetes auth backend with management cluster API server + CA, define `gitopsapi` policy, write credentials.

#### What Is Eliminated Versus Scenario 2

| Item | Scenario 2 | Scenario 3 |
|---|---|---|
| `GITHUB_TOKEN` at rest | K8s Secret in `gitopsapi` namespace | In OpenBao only; retrieved at runtime |
| Management SOPS age key (private) | K8s Secret in `flux-system` | In OpenBao; never in K8s Secret |
| Credential audit trail | None | Full — every read in OpenBao audit log |
| SOPS key backup/recovery | Manual; no DR path | OpenBao raft backend provides durability and DR |
| Key rotation | Manual | OpenBao rotation policies; automatable |

#### Blast Radius — Namespace Compromise (Scenario 3)

If the `gitopsapi` namespace is compromised:

- Attacker gains the ServiceAccount JWT — short-lived and scoped only to the `gitopsapi` OpenBao policy
- `GITHUB_TOKEN` is **not present** in the namespace at rest — must be fetched from OpenBao
- SOPS management age key is **not present** in the namespace — stored in OpenBao
- The OpenBao audit log captures any fetch, enabling incident detection

**Blast radius reduced from "entire platform" to "what gitopsapi currently holds in process memory"** — a single process scope, bounded in time, auditable.

#### Enterprise Fit

| Enterprise Requirement | Capability |
|---|---|
| Audit trail | ✅ Full audit log — every secret read timestamped and attributed |
| Key rotation | ✅ OpenBao rotation policies + dynamic secrets |
| HSM-backed key storage | ✅ OpenBao supports PKCS#11 HSM seal |
| Multi-tenancy | ✅ OpenBao namespaces allow per-tenant isolation |
| Compliance (SOC 2, ISO 27001) | ✅ Audit logs + access controls + rotation = compliant posture |
| Disaster recovery | ✅ Raft backend snapshot + unseal key management |

**Verdict:** Enterprise-ready. Aligns with standard Vault patterns used in production Kubernetes platforms.

#### Migration Path: Scenario 2 → Scenario 3

1. Deploy OpenBao to platform-services (CC-081)
2. Initialise and unseal; store unseal keys and root token in an offline safe
3. Enable Kubernetes auth backend; point at management cluster API server
4. Write existing credentials to OpenBao (`secret/gitopsapi/github-token`, `secret/gitopsapi/sops-mgmt-key`, etc.)
5. Update gitopsapi Helm values with `OPENBAO_ADDR`, `OPENBAO_ROLE`
6. Add OpenBao client (`hvac`) to gitopsapi; implement `VaultCredentialStore`
7. Update startup in `api/main.py` to fetch credentials from OpenBao when `GITOPS_VAULT_ENABLED=1`
8. Remove K8s Secrets for rotated credentials once confirmed working
9. Optionally: enable OpenBao dynamic secrets for GitHub tokens via the GitHub Secrets Engine

Migration is incremental — credentials can be migrated one at a time.

---

## 3. Recommendations

### 3.1 Recommended Scenario by Deployment Context

| Deployment Context | Recommendation | Rationale |
|---|---|---|
| **Developer / hobbyist** (single-node, self-managed) | Scenario 1 | SOPS/OpenBao complexity outweighs benefit at this scale |
| **Small enterprise** (on-prem, small team, no hard compliance requirements) | Scenario 2 | SOPS provides meaningful at-rest protection; management age key backup must be documented |
| **Enterprise** (multi-tenant, compliance requirements) | Scenario 3 | Full audit trail, HSM-grade keys, rotation automation, per-tenant isolation |

For PodZone specifically:

- **Now:** Scenario 2 (partially implemented — SOPS keys exist per cluster)
- **Medium term:** Scenario 3 for platform-services and partner deployments (OpenBao via CC-081)
- **Getting-started docs:** Document Scenario 1 clearly as the "no-config" path

### 3.2 Changes Required to Support Scenario 3

#### New Configuration

| Env Var | Purpose |
|---|---|
| `OPENBAO_ADDR` | OpenBao server URL |
| `OPENBAO_ROLE` | Kubernetes auth role name |
| `OPENBAO_SECRET_PATH` | Base path for gitopsapi secrets (e.g., `secret/gitopsapi`) |
| `GITOPS_VAULT_ENABLED` | Feature flag — activates OpenBao credential retrieval |

#### New Dependency

- Python `hvac` library (Vault/OpenBao API client)

#### Code Changes

1. New `VaultCredentialStore` wrapping `hvac.Client` with Kubernetes auth
2. Existing `EnvCredentialStore` reading from environment (Scenario 1/2 fallback)
3. Startup in `api/main.py`: select store based on `GITOPS_VAULT_ENABLED`; inject into services
4. Update `git_service`, `github_service`, `sops_service`, `deploy_key_service` to accept credentials from the store rather than reading env vars directly

#### Interface Contract

```python
class CredentialStore(Protocol):
    def get_secret(self, path: str) -> str: ...

class VaultCredentialStore:
    """OpenBao / HashiCorp Vault (hvac)."""

class EnvCredentialStore:
    """Reads from environment variables — Scenario 1/2 fallback."""
```

### 3.3 Keystore-Agnostic vs Vault-Specific

**Recommendation: Vault-API-compatible abstraction, defaulting to OpenBao.**

OpenBao is API-compatible with HashiCorp Vault — the same `hvac` client works for both. A fully agnostic interface supporting AWS SM, Azure KV, etc. adds significant complexity for limited current benefit. The `CredentialStore` protocol above makes it straightforward to add adaptors later without committing to that complexity now.

---

## 4. Current Security Gaps (All Scenarios)

The following issues exist regardless of scenario and should be addressed:

| # | Issue | Severity | Location | Recommendation |
| :--- | :--- | :--- | :--- | :--- |
| G-001 | SSH host key verification disabled (`StrictHostKeyChecking=no`) | HIGH | git_service.py:36 | Maintain `known_hosts` and enable host key verification |
| G-002 | `GITHUB_TOKEN` embedded in HTTPS clone URLs | MEDIUM | git_service.py:43 | Consider GitHub App authentication; tokens in URLs appear in git error output and server logs |
| G-003 | Management kubeconfig written to `/tmp` (world-readable directory) | MEDIUM | main.py:21 | Use `tempfile.mkdtemp(mode=0o700)` or keep in memory only |
| G-004 | `MCP_AUTH_TOKEN` optional — MCP endpoint can be unauthenticated | MEDIUM | context_server.py:315 | Require token in production; validate at startup and fail if not set |
| G-005 | No audit logging of credential access | MEDIUM | Multiple | Add structured audit log entry on each credential read/use |
| G-006 | `GITOPSGUI_DEV_ROLE` has no production guard | MEDIUM | auth.py:41 | Fail startup (or log prominent warning) if set outside dev mode |
| G-007 | SSH deploy keys generated in unprotected tempdir | LOW | deploy_key_service.py:44 | Use `tempfile.mkdtemp(mode=0o700)` to restrict directory permissions during generation |
| G-008 | No token rotation or expiry mechanism | LOW | Multiple | Document rotation runbook; implement expiry tracking annotation on generated keys |

---

*Prepared by Heph — 2026-03-22. Codebase audit: `src/gitopsgui/` v0.1.6. Task: CC-082.*
