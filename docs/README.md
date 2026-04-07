# GitOpsAPI Helm Chart Repository

**Repository URL:** https://podzoneplatformengineering.github.io/gitopsapi

## Adding the Helm Repository

```bash
helm repo add gitopsapi https://podzoneplatformengineering.github.io/gitopsapi
helm repo update
```

## Installing GitOpsAPI

```bash
helm install gitopsapi gitopsapi/gitopsapi \
  --namespace gitopsapi \
  --create-namespace \
  --values custom-values.yaml
```

## Chart Versions

- **v0.1.0** — Initial release

**Chart Source:** `charts/gitopsapi/` in this repository
