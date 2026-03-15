# Promotion Pipeline

GitOpsAPI manages application deployments through a gated promotion pipeline: dev → ETE → prod.

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
- **Required approvals**: Build Manager + Release Manager
- Artifact deployed to gitopsprod cluster

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

## Promoting an Artifact

### Via GitOpsGUI (when available)

1. Open change pipeline in GitOpsGUI
2. Select release to promote
3. Click "Promote to ETE" / "Promote to Production"
4. Approval workflow triggered automatically

### Via PR (bootstrap/manual)

Until GitOpsGUI is operational, promotion is via direct PR to cluster09:

**Dev → ETE**:

```bash
# Add application to gitopsete kustomization
# clusters/gitopsete/gitopsete-apps.yaml
# Update chart version in gitops/gitops-apps/<app>/<app>-values.yaml
# Create PR, get Build Manager approval, merge
```

**ETE → Prod**:

```bash
# Add application to gitopsprod kustomization
# clusters/gitopsprod/gitopsprod-apps.yaml
# Create PR, get Build Manager + Release Manager approval, merge
```

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
  "chartRepository": "https://motttt.github.io/gitopsapi"
}
```

## Rollback

Production issues trigger rollback + new dev cycle:

1. Revert gitopsprod to previous chart version (PR, immediate approval)
2. Root cause analysis on gitopsdev
3. Fix → re-promote through full pipeline

No direct hotfixes to production — all fixes go through dev → ETE → prod.
