# GitOpsAPI Deployment Prerequisites

**Version**: V0.2.0
**Updated**: 2026-03-12

This checklist must be completed before deploying or updating GitOpsAPI. The pod will fail to start if any of the required secrets are missing.

---

## Required Kubernetes Secrets

All secrets must be created in the `gitopsapi` namespace **before** the HelmRelease is applied.

### 1. `gitopsapi-ssh-key` — Git repository SSH key

**Purpose**: Authenticates GitOpsAPI to the GitOps repository (`cluster09` or per-cluster repos) for all read/write operations.
**Blocks pod start**: Yes — mounted as a volume; pod will not start if missing.

```bash
kubectl create secret generic gitopsapi-ssh-key \
  --namespace gitopsapi \
  --from-file=id_rsa=<path-to-private-key>
```

**Decision required**: Use a dedicated deploy key (recommended) or the shared git SSH key.

- Dedicated deploy key: generate with `ssh-keygen -t ed25519 -f gitopsapi-deploy-key -C "gitopsapi@podzone"`, add the public key as a deploy key on the GitHub repo with write access.
- Shared key: use the existing key from `~/.ssh/id_rsa` or equivalent.

---

### 2. `gitopsapi-github-token` — GitHub Personal Access Token

**Purpose**: Used by GitOpsAPI to create pull requests and manage branches on the GitOps repository via the GitHub API.
**Required scope**: `repo` (full repository access on `MoTTTT/cluster09`, or the relevant per-cluster repo).

```bash
kubectl create secret generic gitopsapi-github-token \
  --namespace gitopsapi \
  --from-literal=token=<github-pat>
```

**Decision required**: Use an existing PAT or create a new one scoped to the GitOps repo only (recommended for least-privilege).

---

### 3. `gitopsapi-mgmt-kubeconfig` — Management cluster kubeconfig

**Purpose**: Allows GitOpsAPI to connect to the Management cluster (CAPI controller) to query cluster status and extract per-cluster kubeconfigs.
**Used for**: `KubeconfigService`, per-cluster K8s API access (V0.2.0 bootstrap).

```bash
kubectl create secret generic gitopsapi-mgmt-kubeconfig \
  --namespace gitopsapi \
  --from-file=kubeconfig=<path-to-mgmt-kubeconfig>
```

To obtain the Management cluster kubeconfig:

```bash
# From the Management cluster control plane node
kubectl get secret <cluster-name>-kubeconfig -n default -o jsonpath='{.data.value}' | base64 -d > mgmt-kubeconfig
```

Or copy from `~/.kube/config` if the management cluster context is already configured locally.

---

### 4. `gitopsapi-age-key` — SOPS age private key

**Purpose**: Used by GitOpsAPI to encrypt secrets (e.g. per-cluster kubeconfigs) before storing them in the GitOps repository.
**Key file format**: age secret key file (begins with `# created: ...`, `# public key: ...`, `AGE-SECRET-KEY-...`).

```bash
kubectl create secret generic gitopsapi-age-key \
  --namespace gitopsapi \
  --from-file=key.txt=<path-to-age-key>
```

**Decision required**: Use the existing key from `~/.config/sops/age/keys.txt`, or generate a new dedicated key:

```bash
age-keygen -o gitopsapi-age-key.txt
# Add the public key to .sops.yaml in the GitOps repo
```

---

## Verification

After creating all secrets, verify they exist before deploying:

```bash
kubectl get secrets -n gitopsapi
# Expected output includes:
# gitopsapi-ssh-key
# gitopsapi-github-token
# gitopsapi-mgmt-kubeconfig
# gitopsapi-age-key
```

---

## Deployment

Once secrets are in place, apply the HelmRelease:

```bash
# Via Flux (preferred) — commit HelmRelease to GitOps repo
# or direct install for testing:
helm install gitopsapi oci://ghcr.io/motttt/gitopsapi \
  --namespace gitopsapi \
  --create-namespace \
  --version 0.2.0
```

Or via the GitHub Pages Helm repository:

```bash
helm repo add gitopsapi https://motttt.github.io/gitopsapi
helm install gitopsapi gitopsapi/gitopsapi --namespace gitopsapi --create-namespace
```

---

## Post-Deployment Checks

```bash
# Check pod is running
kubectl get pods -n gitopsapi

# Check liveness (process running)
curl -H "Host: gitopsgui.podzone.cloud" http://freyr:8081/health

# Check readiness (git init complete)
curl -H "Host: gitopsgui.podzone.cloud" http://freyr:8081/ready

# Check logs if pod is not ready
kubectl logs -n gitopsapi deploy/gitopsapi
```

**Note**: All requests to the GitOpsAPI via the Gateway require the `Host: gitopsgui.podzone.cloud` header. See [run-tests.sh](../tests/test_data/run-tests.sh) for example curl commands.

---

## Environment Variables (Helm chart managed)

The following environment variables are injected from secrets by the Helm chart (`charts/gitopsapi/templates/deployment.yaml`). These do not need to be set manually.

| Env var | Source secret | Key | Purpose |
| --- | --- | --- | --- |
| `GITHUB_TOKEN` | `gitopsapi-github-token` | `token` | GitHub API auth |
| `MGMT_KUBECONFIG_SECRET` | `gitopsapi-mgmt-kubeconfig` | `kubeconfig` | Management cluster access |
| `SOPS_AGE_KEY_SECRET` | `gitopsapi-age-key` | `key.txt` | SOPS encryption |
| `SSH_PRIVATE_KEY` | `gitopsapi-ssh-key` | `id_rsa` | Git SSH auth |
