#!/usr/bin/env bash
set -euo pipefail
# Required env vars:
#   KUBECONFIG_PATH            path to kubeconfig file
#   CLUSTER_CHART_REPO_URL     e.g. "oci://ghcr.io/podzoneplatformengineering/cluster-chart"
#   CLUSTER_CHART_VERSION      e.g. "0.1.40"

export KUBECONFIG="$KUBECONFIG_PATH"

# Install helm if not present
if ! command -v helm &>/dev/null; then
  echo "Installing helm..." >&2
  curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash >&2
fi

# Install flux CLI if not present
if ! command -v flux &>/dev/null; then
  echo "Installing flux CLI..." >&2
  curl -s https://fluxcd.io/install.sh | bash >&2
fi

# Install Flux
if ! flux check --pre 2>/dev/null | grep -q "flux 2"; then
  flux install
fi

# Install CAPI
if ! helm status cluster-api -n capi-system &>/dev/null; then
  helm install cluster-api \
    oci://ghcr.io/rancher/cluster-api-operator/charts/cluster-api-operator \
    --namespace capi-system --create-namespace \
    --set infrastructure=proxmox \
    --wait --timeout 5m
fi

# Install CAPMOX
if ! helm status capmox -n capmox-system &>/dev/null; then
  helm install capmox \
    oci://ghcr.io/ionos-cloud/cluster-api-provider-proxmox/charts/cluster-api-provider-proxmox \
    --namespace capmox-system --create-namespace \
    --wait --timeout 5m
fi

echo "{\"status\":\"complete\",\"flux\":\"installed\",\"capi\":\"installed\",\"capmox\":\"installed\"}"
