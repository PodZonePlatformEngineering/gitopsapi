# Getting Started with GitOpsAPI

This guide walks through the **minimum viable installation** — running GitOpsAPI with the public catalog, then progressively adding capabilities.

---

## Overview

GitOpsAPI manages Kubernetes clusters and their workloads through a REST API backed by Git. All writes create a pull request; nothing commits directly to `main`. You interact with clusters, applications, and pipelines as first-class API objects.

Three installation modes:

| Mode | What you get | Secrets required |
| --- | --- | --- |
| **Read-only catalog** | Browse and read the application catalog | None |
| **External cluster management** | Add existing clusters, assign apps, manage PRs | GitHub token |
| **Full provisioning** | Provision new clusters via CAPI on bare-metal/cloud | GitHub token + kubeconfig + SOPS key |

Start with read-only, add secrets as you need each capability.

---

## Prerequisites

- A Kubernetes cluster (any distribution — k3s, Talos, EKS, GKE, etc.)
- `helm` ≥ 3.12 and `kubectl`
- Optional: a GitHub organisation and Personal Access Token (for write operations)

---

## Step 1 — Install the Helm chart

Add the GitOpsAPI Helm repository:

```bash
helm repo add gitopsapi https://motttt.github.io/gitopsapi
helm repo update
```

Install into the `gitopsapi` namespace:

```bash
helm install gitopsapi gitopsapi/gitopsapi \
  --namespace gitopsapi \
  --create-namespace
```

The application starts immediately. Without any secrets, it serves the public catalog from `gitopsapi-apps` (read-only). The `/health` and `/ready` endpoints return 200.

---

## Step 2 — Verify the installation

```bash
# Check the pod is running
kubectl get pods -n gitopsapi

# Port-forward for local access
kubectl port-forward -n gitopsapi svc/gitopsapi 8000:8000

# Browse the API docs
open http://localhost:8000/docs

# Health check
curl http://localhost:8000/health
```

---

## Step 3 — Enable write operations (optional)

Write operations (create cluster, assign application, raise PR) require a GitHub Personal Access Token with `repo` scope.

Create the secret:

```bash
kubectl create secret generic gitopsapi-github-token \
  --namespace gitopsapi \
  --from-literal=token=<your-github-pat>
```

Configure your GitOps repository in the Helm values:

```yaml
# my-values.yaml
gitops:
  repoUrl: "https://github.com/<your-org>/<your-mgmt-repo>.git"
  githubOrg: "<your-org>"
  githubRepo: "<your-mgmt-repo>"
  branch: main
```

Upgrade the release:

```bash
helm upgrade gitopsapi gitopsapi/gitopsapi \
  --namespace gitopsapi \
  --values my-values.yaml
```

---

## Step 4 — Enable cluster status reads (optional)

To read live cluster status, provide the management cluster kubeconfig:

```bash
kubectl create secret generic gitopsapi-mgmt-kubeconfig \
  --namespace gitopsapi \
  --from-file=kubeconfig=<path-to-mgmt-kubeconfig>
```

---

## Step 5 — Enable SOPS encryption (optional)

GitOpsAPI encrypts sensitive values (per-cluster kubeconfigs, credentials) before committing to Git using [SOPS](https://getsops.io/) with age keys.

Generate a key:

```bash
age-keygen -o gitopsapi-age-key.txt
# Note the public key line: # public key: age1...
```

Register the public key in the management GitOps repo's `.sops.yaml`:

```yaml
creation_rules:
  - path_regex: .*\.yaml
    age: age1<your-public-key>
```

Create the secret:

```bash
kubectl create secret generic gitopsapi-age-key \
  --namespace gitopsapi \
  --from-file=key.txt=gitopsapi-age-key.txt
```

---

## Step 6 — Enable cluster provisioning (optional)

To provision new clusters via CAPI, configure the hypervisor (Proxmox or other CAPI provider) in the Helm values:

```yaml
# my-values.yaml
gitops:
  repoUrl: "https://github.com/<your-org>/<your-mgmt-repo>.git"
  githubOrg: "<your-org>"
  githubRepo: "<your-mgmt-repo>"
```

Then use `POST /api/v1/clusters` with a `ClusterSpec` that includes the platform details:

```json
{
  "name": "my-cluster",
  "platform": {
    "name": "<hypervisor-name>",
    "type": "proxmox",
    "endpoint": "https://<proxmox-host>:8006",
    "nodes": ["<proxmox-node>"],
    "template_vmid": 100
  },
  "vip": "<control-plane-vip>",
  "ip_range": "<start-ip>-<end-ip>",
  "dimensions": {
    "control_plane_count": 1,
    "worker_count": 2
  }
}
```

GitOpsAPI writes a cluster-chart values file and raises a PR to your management repo. Flux and CAPI pick it up and provision the cluster.

---

## Next Steps

- [Deployment prerequisites](deployment-prerequisites.md) — full secrets reference
- [API reference](http://localhost:8000/docs) — interactive OpenAPI docs
- [Application catalog](application-catalog.md) — available applications
- [Roles reference](roles-reference.md) — user roles and permissions
- [API-first testing protocol](api-first-testing-protocol.md) — testing guide
