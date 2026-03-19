# GitOpsAPI Roles Reference

GitOpsAPI uses role-based access control (RBAC). Roles are injected via OAuth2 proxy headers (`X-Auth-Request-Groups`) and mapped to permissions in `src/gitopsgui/api/auth.py`.

---

## Roles

### `bootstrap_admin`

The initial administrator role. Created automatically during installation.

**Permissions:**

- All `cluster_operator` permissions
- Configure external authentication provider (Keycloak)
- Create and manage user assignments
- Manage platform (hypervisor) registrations

**Use for:** Initial setup, authentication configuration, platform onboarding.

---

### `cluster_operator`

Manages the provisioning and lifecycle of clusters.

**Permissions:**

- `POST /api/v1/clusters` — provision a new cluster
- `PUT /api/v1/clusters/{name}` — update cluster spec
- `DELETE /api/v1/clusters/{name}` — decommission a cluster
- `POST /api/v1/clusters/{name}/suspend` — suspend cluster scheduling
- `POST /api/v1/clusters/{name}/decommission` — mark cluster for decommission
- `POST /api/v1/repositories` — create cluster GitOps repositories
- `POST /api/v1/repositories/{name}/deploy-key` — manage deploy keys
- All read operations

**Use for:** Infrastructure team members responsible for cluster lifecycle.

---

### `build_manager`

Manages the promotion pipeline — reviewing, approving, and merging PRs between environments.

**Permissions:**

- `GET /api/v1/prs` — list pull requests
- `GET /api/v1/prs/{id}` — inspect pull request
- `POST /api/v1/prs/{id}/approve` — approve a PR
- `POST /api/v1/prs/{id}/merge` — merge a PR
- `POST /api/v1/pipelines` — create promotion pipelines
- All read operations

**Use for:** Release managers and platform engineers governing the dev → ETE → production flow.

---

### `software_developer`

Creates and manages applications and assigns them to clusters.

**Permissions:**

- `POST /api/v1/applications` — register an application
- `PUT /api/v1/applications/{name}` — update an application
- `POST /api/v1/application-configs` — assign application to a cluster
- `PATCH /api/v1/application-configs/{id}` — update assignment configuration
- All read operations

**Use for:** Development team members deploying workloads to clusters.

---

## Role Mapping

Groups are mapped to roles via the `X-Auth-Request-Groups` header. The default mapping:

| Group | Role |
| --- | --- |
| `cluster-operators` | `cluster_operator` |
| `build-managers` | `build_manager` |
| `software-developers` | `software_developer` |
| `admins` | `bootstrap_admin` |

Group-to-role mapping is configured in `src/gitopsgui/api/auth.py` (`_GROUP_TO_ROLE`).

---

## Authentication Providers

GitOpsAPI accepts identity from an OAuth2 proxy. The proxy injects `X-Auth-Request-Groups` and `X-Forwarded-User` headers after authenticating the user.

**Supported providers:** Keycloak (configured at install time). OIDC-compatible providers supportable via the same header injection pattern.

**Dev bypass:** Set `GITOPSGUI_DEV_ROLE=cluster_operator` (or any role) to skip auth checks entirely. **Never use in production.**

---

## Read Operations (unauthenticated in read-only mode)

The following endpoints are available without authentication when the application is in catalog-read-only mode (no writable repo configured):

- `GET /api/v1/status` — health and readiness
- `GET /api/v1/applications` — list catalog applications
- `GET /api/v1/clusters` — list clusters
