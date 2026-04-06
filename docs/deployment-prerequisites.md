# GitOpsAPI Deployment Prerequisites

**Updated**: 2026-03-19

This checklist covers secrets and configuration required before deploying GitOpsAPI.
Secrets are optional — the application starts without them and degrades gracefully:
read-only catalog mode requires no secrets.

---

## Secrets

All secrets must be created in the same namespace as the Helm release (default: `gitopsapi`).

### 1. `gitopsapi-github-token` — GitHub Personal Access Token

**Purpose**: Creates pull requests and manages branches on the GitOps repository via the GitHub API.
**Required for**: Any write operation (cluster create/update, application assignment, pipeline create).
**Required scope**: `repo` (full repository access on your management and per-cluster repos).

```bash
kubectl create secret generic gitopsapi-github-token \
  --namespace gitopsapi \
  --from-literal=token=<github-pat>
```

Create a PAT at GitHub → Settings → Developer settings → Personal access tokens.
Recommend a fine-grained token scoped to your GitOps organisation and repos.

---

### 2. `gitopsapi-mgmt-kubeconfig` — Management cluster kubeconfig

**Purpose**: Connects to the Management cluster (CAPI controller) to query cluster status and extract per-cluster kubeconfigs.
**Required for**: `GET /api/v1/clusters` live status; per-cluster kubeconfig retrieval.

```bash
kubectl create secret generic gitopsapi-mgmt-kubeconfig \
  --namespace gitopsapi \
  --from-file=kubeconfig=<path-to-mgmt-kubeconfig>
```

To obtain the kubeconfig from a CAPI-managed cluster:

```bash
kubectl get secret <cluster-name>-kubeconfig \
  -n <capi-namespace> \
  -o jsonpath='{.data.value}' | base64 -d > mgmt-kubeconfig
```

---

### 3. `gitopsapi-age-key` — SOPS age private key

**Purpose**: Encrypts sensitive values (per-cluster kubeconfigs, credentials) before committing them to the GitOps repository.
**Required for**: Cluster provisioning with SOPS-encrypted secrets; `sops_secret_ref` in `ClusterSpec`.
**Key format**: age secret key file (begins with `# created: ...` / `AGE-SECRET-KEY-...`).

```bash
kubectl create secret generic gitopsapi-age-key \
  --namespace gitopsapi \
  --from-file=key.txt=<path-to-age-key>
```

Generate a new age key:

```bash
age-keygen -o gitopsapi-age-key.txt
# Register the public key in .sops.yaml in your management GitOps repo
```

---

## Verification

```bash
kubectl get secrets -n gitopsapi
# Should include whichever of these you have created:
# gitopsapi-github-token
# gitopsapi-mgmt-kubeconfig
# gitopsapi-age-key
```

---

## Helm Values

Configure the GitOps repository and GitHub organisation via Helm values:

```yaml
# my-values.yaml
gitops:
  catalogRepoUrl: "https://github.com/<your-org>/gitopsapi-apps.git"  # read-only catalog; required if using application catalog
  repoUrl: "https://github.com/<your-org>/<your-mgmt-repo>.git"       # writable management repo
  githubOrg: "<your-org>"
  githubRepo: "<your-mgmt-repo>"
  branch: main

expose:
  hostname: <your-hostname>
  gatewayName: <gateway-name>
  gatewayNamespace: <gateway-namespace>
  tlsSecretName: <tls-secret-name>
```

Apply with:

```bash
helm upgrade --install gitopsapi gitopsapi/gitopsapi \
  --namespace gitopsapi \
  --create-namespace \
  --values my-values.yaml
```

---

## Deployment

Install from GitHub Pages Helm repository:

```bash
helm repo add gitopsapi https://podzoneplatformengineering.github.io/gitopsapi
helm repo update
helm install gitopsapi gitopsapi/gitopsapi \
  --namespace gitopsapi \
  --create-namespace \
  --values my-values.yaml
```

---

## Post-Deployment Checks

```bash
# Check pod is running
kubectl get pods -n gitopsapi

# Port-forward for direct access
kubectl port-forward -n gitopsapi svc/gitopsapi 8000:8000

# Health check (process running)
curl http://localhost:8000/health

# Readiness check (git init complete)
curl http://localhost:8000/ready

# Browse API docs
open http://localhost:8000/docs
```

---

## Environment Variables (Helm chart managed)

The Helm chart injects these environment variables from secrets. They do not need to be set manually.

| Env var | Source secret | Key | Purpose |
| --- | --- | --- | --- |
| `GITOPS_CATALOG_REPO_URL` | Helm value | `gitops.catalogRepoUrl` | Read-only catalog source |
| `GITOPS_REPO_URL` | Helm value | `gitops.repoUrl` | Writable management repo |
| `GITHUB_TOKEN` | `gitopsapi-github-token` | `token` | GitHub API auth |
| `MGMT_KUBECONFIG_SECRET` | `gitopsapi-mgmt-kubeconfig` | `kubeconfig` | Management cluster access |
| `SOPS_AGE_KEY_SECRET` | `gitopsapi-age-key` | `key.txt` | SOPS encryption |
