# Promotion Pipeline

GitOpsAPI manages application deployments through a gated promotion pipeline: dev → ETE → prod.
All mutations flow through git: the API writes manifests to feature branches, opens PRs for human
review and approval, and relies on Flux CD to reconcile approved state onto target clusters.

## Multi-Repo Architecture

Each cluster has **two** git repositories:

| Repo | Purpose | PR target for |
| ---- | ------- | ------------- |
| `{cluster}-infra` | Flux bootstrap manifests, infrastructure Kustomizations, cluster-specific config | Flux Kustomization entries (e.g. `clusters/{cluster}/{cluster}-apps.yaml`) |
| `{cluster}-apps` | Application HelmReleases, values, Kustomization entries | Application changes: HelmRelease at `gitops/gitops-apps/{name}/{name}.yaml`, values override at `gitops/gitops-apps/{name}/{name}-values-{cluster}.yaml` |

A third repo, `cluster-charts` (the management repo), holds cluster provisioning definitions.
PRs for cluster lifecycle operations (provision, suspend, decommission) target this repo.

Routing between repos is handled by `src/gitopsgui/services/repo_router.py`.
URL convention: `git@github.com:{GITHUB_ORG}/{cluster}-apps.git` and `git@github.com:{GITHUB_ORG}/{cluster}-infra.git`.

## Pipeline Stages

### Stage 1: Development (gitopsdev cluster)

- New release deployed to gitopsdev cluster
- Dev team tests and verifies functionality
- No approval gates — dev team controls deployment cadence
- Multiple iterations until dev verification passes
- **Gate**: Dev team declares version ready for ETE

### Stage 2: End-to-End Testing (gitopsete cluster)

- **Trigger**: Dev verification passes
- Artifact promoted to gitopsete cluster
- Full integration and E2E testing conducted
- **Required approval**: Build Manager (via GitOpsGUI)
- **Gate**: ETE verification must pass

### Stage 3: Production (gitopsprod cluster)

- **Trigger**: ETE verification passes + approvals received
- **Required approvals**: Build Manager + Cluster Operator
- Artifact deployed to gitopsprod cluster

## Stage Labels and Approver Rules

PRs raised by GitOpsAPI carry a `stage:` label that drives required-approver enforcement:

| Label | Stage | Required approvers |
| ----- | ----- | ------------------ |
| `stage:dev` | Development | `build_manager` |
| `stage:ete` | End-to-End Testing | `build_manager` |
| `stage:production` | Production | `build_manager` + `cluster_operator` |

Approver rules are enforced at two levels:

1. **API layer** — `POST /api/v1/prs/{pr_number}/approve` checks the caller's role against
   the stage label before calling the git forge approve API.
2. **Git forge layer** — branch protection rules on the target repo enforce the same approval
   matrix (BR-AUTH-004).

## PR Endpoints

| Method | Endpoint | Description | Roles |
| ------ | -------- | ----------- | ----- |
| GET | `/api/v1/prs` | List PRs (filter by `state`, `label`) | cluster_operator, build_manager, senior_developer |
| GET | `/api/v1/prs/{pr_number}` | Get PR detail | cluster_operator, build_manager, senior_developer |
| POST | `/api/v1/prs/{pr_number}/approve` | Approve PR (role and stage checked) | cluster_operator, build_manager |
| POST | `/api/v1/prs/{pr_number}/merge` | Merge PR (requires approvals satisfied) | cluster_operator, build_manager |

## What Raises a PR Where

| Operation | Target repo | Label |
| --------- | ----------- | ----- |
| Assign application to cluster | `{cluster}-apps` | `stage:{pipeline_stage}` |
| Update cluster-specific values | `{cluster}-apps` | `stage:{pipeline_stage}` |
| Remove application from cluster | `{cluster}-apps` | `stage:{pipeline_stage}` |
| Add Flux Kustomization entry | `{cluster}-infra` | `stage:{pipeline_stage}` |
| Provision cluster | `cluster-charts` (management) | `stage:production` |
| Suspend cluster | `cluster-charts` (management) | `stage:production` |
| Decommission cluster | `cluster-charts` (management) | `stage:production` |

## Parallel Development

While gitopsprod serves customers with release N, gitopsdev can begin testing release N+1:

```text
T0: v1.0 deployed to gitopsdev
T1: v1.0 promoted to gitopsete (ETE testing)
T2: v1.0 promoted to gitopsprod (serving customers)
    v1.1 starts dev testing on gitopsdev
T3: v1.1 promoted to gitopsete
    v1.2 starts dev testing on gitopsdev
```

Each change pipeline instance is independent — concurrent releases at different stages do not block each other.

## Rollback

Production issues trigger rollback + new dev cycle:

1. Revert gitopsprod to previous chart version (PR, immediate approval)
2. Root cause analysis on gitopsdev
3. Fix → re-promote through full pipeline

No direct hotfixes to production — all fixes go through dev → ETE → prod.

## Example: GitOpsAPI v0.1.0 Promotion

```text
Phase 1 (Bootstrap):  Deploy v0.1.0 to openclaw (temporary, for API testing)
Phase 2 (Dev):        Deploy v0.1.0 to gitopsdev via manifest commit
Phase 3 (Self-mgmt):  Use GitOpsAPI to promote v0.1.0 to gitopsete
Phase 4 (Prod):       Promote v0.1.0 to gitopsprod after ETE passes
Phase 5 (Cleanup):    Remove from openclaw bootstrap deployment
```

## GitOpsAPI Change Pipeline Specification

```json
{
  "name": "gitopsapi-v0.1.0",
  "application": "gitopsapi",
  "chartVersion": "0.1.0",
  "devCluster": "gitopsdev",
  "eteCluster": "gitopsete",
  "prodCluster": "gitopsprod",
  "chartRepository": "https://podzoneplatformengineering.github.io/gitopsapi"
}
```
