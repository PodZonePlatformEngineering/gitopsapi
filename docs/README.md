# GitOpsAPI Helm Chart Repository

**Repository URL:** https://motttt.github.io/gitopsapi

## Adding the Helm Repository

```bash
helm repo add gitopsapi https://motttt.github.io/gitopsapi
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

## GitHub Pages Setup (One-Time)

1. Go to: [github.com/PodZone/gitopsapi/settings/pages](https://github.com/PodZone/gitopsapi/settings/pages)
2. **Source:** Deploy from a branch
3. **Branch:** main
4. **Folder:** /docs
5. Click **Save**

GitHub Pages will be available at: https://motttt.github.io/gitopsapi

---

**Chart Source:** `charts/gitopsapi/` in this repository
