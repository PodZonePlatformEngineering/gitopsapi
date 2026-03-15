# Application Catalog

Seed catalog for GitOpsAPI. Extracted from cluster09 GitOps repos, gitopsdev-apps, planning tasks, and architecture docs.

**Schema**: Each entry uses the GitOpsAPI Application Specification fields where known.
**`⚠️ RECOMMENDED`**: Fields marked with ⚠️ are unknown/to be confirmed.

---

## Deployed Applications

### gitopsapi

**Description**: GitOps platform API — manages application lifecycle, cluster configuration, change pipelines, and promotion across dev/ETE/prod.
**GitHub**: https://github.com/MoTTTT/gitopsapi
**Web**: https://motttt.github.io/gitopsapi
**Category**: Platform Management, Internal Tooling
**Activity**: 2 clusters (openclaw — bootstrap; gitopsdev — target)

| Field | Value |
|---|---|
| name | gitopsapi |
| helmRepo | https://motttt.github.io/gitopsapi |
| chart | gitopsapi |
| chartVersion | 0.1.2 (gitopsdev-apps), 0.1.1 (cluster09) |
| namespace | gitopsapi |
| applicationRepo | https://github.com/MoTTTT/gitopsapi |
| valuesFile | gitopsapi-values ConfigMap |

---

### gitopsgui

**Description**: GitOps platform GUI — React frontend for GitOpsAPI. Provides approval workflows, pipeline visualisation, cluster and application management.
**GitHub**: ⚠️ RECOMMENDED: https://github.com/MoTTTT/gitopsgui (confirm)
**Web**: ⚠️ RECOMMENDED: https://gui.podzone.cloud (confirm)
**Category**: Platform Management, Internal Tooling
**Activity**: 0 (planned — gitopsdev first)

| Field | Value |
|---|---|
| name | gitopsgui |
| helmRepo | ⚠️ RECOMMENDED: https://motttt.github.io/gitopsgui |
| chart | ⚠️ RECOMMENDED: gitopsgui |
| chartVersion | ⚠️ RECOMMENDED: 0.1.0 |
| namespace | gitopsgui |
| applicationRepo | ⚠️ RECOMMENDED: https://github.com/MoTTTT/gitopsgui |

---

### keycloak

**Description**: Identity and access management — OIDC provider, realm management, user federation. Secures GitOpsAPI and platform services.
**GitHub**: https://github.com/keycloak/keycloak
**Web**: https://www.keycloak.org
**Category**: Security, Identity
**Activity**: 1 cluster (cluster09/openclaw — StatefulSet, not Helm)

| Field | Value |
|---|---|
| name | keycloak |
| helmRepo | ⚠️ RECOMMENDED: https://charts.bitnami.com/bitnami or official |
| chart | ⚠️ RECOMMENDED: keycloak (currently deployed as StatefulSet, migrate to Helm) |
| chartVersion | 26.3.0 (image), ⚠️ chart TBD |
| namespace | security |
| applicationRepo | — |

**Note**: Currently deployed as raw StatefulSet + PostgreSQL deployment (not HelmRelease). Keycloak + embedded postgres are co-deployed in `security` namespace. Migration to HelmRelease + CloudNativePG is planned (SEC-02–SEC-11).

---

### oauth2-proxy

**Description**: OAuth2 reverse proxy — fronts cluster services with OIDC authentication via Keycloak.
**GitHub**: https://github.com/oauth2-proxy/oauth2-proxy
**Web**: https://oauth2-proxy.github.io/oauth2-proxy
**Category**: Security, Networking
**Activity**: 0 (HelmRepository defined, HelmRelease commented out — pending Keycloak readiness)

| Field | Value |
|---|---|
| name | oauth2-proxy |
| helmRepo | https://oauth2-proxy.github.io/manifests |
| chart | oauth2-proxy |
| chartVersion | 7.12.18 |
| namespace | security |

---

### harbor

**Description**: Container registry — stores and distributes Docker images and Helm charts. Internal OCI registry for the platform.
**GitHub**: https://github.com/goharbor/harbor
**Web**: https://goharbor.io
**Category**: Artifact Management, Container Registry
**Activity**: 1 cluster (cluster09)

| Field | Value |
|---|---|
| name | harbor |
| helmRepo | https://helm.goharbor.io/ |
| chart | harbor |
| chartVersion | 1.18.0 |
| namespace | harbor |

---

### qdrant

**Description**: Vector database — stores and queries embeddings for AI context management. Used by GitOpsAPI MCP context server and agent team.
**GitHub**: https://github.com/qdrant/qdrant
**Web**: https://qdrant.tech
**Category**: AI/ML Infrastructure, Data Storage
**Activity**: 1 cluster (cluster09)

| Field | Value |
|---|---|
| name | qdrant |
| helmRepo | https://qdrant.github.io/qdrant-helm |
| chart | qdrant |
| chartVersion | 1.17.0 |
| namespace | qdrant |

---

### ollama

**Description**: Local LLM inference server — serves language models (e.g. nomic-embed-text for embeddings, other models for agent inference).
**GitHub**: https://github.com/ollama/ollama
**Web**: https://ollama.com
**Category**: AI/ML Infrastructure
**Activity**: 1 cluster (cluster09)

| Field | Value |
|---|---|
| name | ollama |
| helmRepo | https://helm.otwld.com/ |
| chart | ollama |
| chartVersion | ⚠️ RECOMMENDED: confirm version (was blank in manifest) |
| namespace | ollama |

---

### opensearch (stack)

**Description**: Distributed search and analytics engine — log storage, indexing, and querying. Deployed as master + data + client node topology.
**GitHub**: https://github.com/opensearch-project/OpenSearch
**Web**: https://opensearch.org
**Category**: Observability, Logging, Search
**Activity**: 1 cluster (cluster09 — 4 HelmReleases: os-master, os-data, os-client, opensearch-dashboards)

| Field | Value |
|---|---|
| name | opensearch |
| helmRepo | https://opensearch-project.github.io/helm-charts/ |
| chart | opensearch (x3 node roles) + opensearch-dashboards |
| chartVersion | 3.0.0 |
| namespace | opensearch |

**Dashboards URL**: https://dashboards.podzone.cloud

---

### opensearch-operator

**Description**: Kubernetes operator for managing OpenSearch clusters declaratively.
**GitHub**: https://github.com/opensearch-project/opensearch-k8s-operator
**Web**: https://opster.github.io/opensearch-k8s-operator-chart
**Category**: Observability, Operators
**Activity**: 1 cluster (cluster09)

| Field | Value |
|---|---|
| name | opensearch-operator |
| helmRepo | https://opster.github.io/opensearch-k8s-operator-chart/ |
| chart | opensearch-operator |
| chartVersion | V1.0 |
| namespace | opensearch-operator |

---

### prometheus (kube-prometheus-stack)

**Description**: Monitoring stack — Prometheus metrics collection, Grafana dashboards, Alertmanager. Full cluster observability.
**GitHub**: https://github.com/prometheus-community/helm-charts
**Web**: https://prometheus.io / https://grafana.com
**Category**: Observability, Monitoring
**Activity**: 1 cluster (cluster09)

| Field | Value |
|---|---|
| name | prometheus |
| helmRepo | https://prometheus-community.github.io/helm-charts |
| chart | kube-prometheus-stack |
| chartVersion | 75.7.0 |
| namespace | prometheus |

**Grafana URL**: https://prometheus.podzone.cloud

---

### fluent-bit

**Description**: Log forwarder — collects pod logs and ships to OpenSearch for storage and analysis.
**GitHub**: https://github.com/fluent/fluent-bit
**Web**: https://fluentbit.io
**Category**: Observability, Logging
**Activity**: 1 cluster (cluster09 — deployed alongside opensearch stack)

| Field | Value |
|---|---|
| name | fluent-bit |
| helmRepo | https://fluent.github.io/helm-charts/ |
| chart | fluent-bit |
| chartVersion | 0.49.1 |
| namespace | opensearch |

---

### openclaw

**Description**: Reverse proxy / gateway — routes external traffic to cluster services. Used as bootstrap host for GitOpsAPI.
**GitHub**: https://github.com/serhanekici/openclaw-helm
**Web**: ⚠️ RECOMMENDED: confirm
**Category**: Networking, Ingress
**Activity**: 1 cluster (cluster09)

| Field | Value |
|---|---|
| name | openclaw |
| helmRepo | https://serhanekicii.github.io/openclaw-helm |
| chart | openclaw-helm |
| chartVersion | 1.3.22 |
| namespace | openclaw |

---

### wso2 (API Manager)

**Description**: API Manager — enterprise API gateway, lifecycle management, developer portal. Currently deployed but review pending for overlap with GitOpsAPI/openclaw.
**GitHub**: https://github.com/wso2/product-apim
**Web**: https://wso2.com/api-manager
**Category**: API Management
**Activity**: 1 cluster (cluster09)

| Field | Value |
|---|---|
| name | wso2 |
| helmRepo | https://helm.wso2.com |
| chart | am-single-node |
| chartVersion | 4.2.0-2 |
| namespace | wso2 |

---

### cert-manager

**Description**: TLS certificate management — automates issuance and renewal of certificates from Let's Encrypt and internal CAs.
**GitHub**: https://github.com/cert-manager/cert-manager
**Web**: https://cert-manager.io
**Category**: Platform Infrastructure, Networking
**Activity**: Multiple clusters (cluster09, management)

| Field | Value |
|---|---|
| name | cert-manager |
| helmRepo | https://charts.jetstack.io |
| chart | cert-manager |
| chartVersion | 1.17.2 |
| namespace | cert-manager |

---

### linstor-affinity-controller (piraeus)

**Description**: Storage affinity controller — manages pod scheduling affinity for Piraeus/LINSTOR replicated storage.
**GitHub**: https://github.com/piraeusdatastore/linstor-affinity-controller
**Web**: https://piraeus.io
**Category**: Platform Infrastructure, Storage
**Activity**: 1 cluster (cluster09 storage layer)

| Field | Value |
|---|---|
| name | affinity-controller |
| helmRepo | https://piraeus.io/helm-charts |
| chart | linstor-affinity-controller |
| chartVersion | ⚠️ RECOMMENDED: confirm version |
| namespace | piraeus-datastore |

---

### cluster09-docs / podzone-docs

**Description**: Static documentation sites — rendered MkDocs sites for cluster09 and podzone platform documentation.
**GitHub**: https://github.com/MoTTTT/charts (static-site chart)
**Web**: ⚠️ RECOMMENDED: confirm hostnames
**Category**: Documentation
**Activity**: 1 cluster each (cluster09)

| Field | Value |
|---|---|
| name | cluster09-docs / podzone-docs |
| helmRepo | https://motttt.github.io/charts/ |
| chart | static-site |
| chartVersion | 0.1.1 |
| namespace | cluster09-docs / podzone-docs |

---

---

### docker-build-agent

**Description**: Docker image build agent — runs containerised Docker builds for gitopsapi and platform images. Deployed on `erectus` (192.168.1.201), the dedicated Docker host. Builds are triggered via rsync + remote docker build; images pushed to GHCR and/or Harbor.
**GitHub**: ⚠️ RECOMMENDED: evaluate Forgejo Actions runner or self-hosted GitHub Actions runner for CI/CD integration
**Web**: ⚠️ RECOMMENDED: confirm agent type (GitHub Actions runner, Forgejo runner, custom)
**Category**: CI/CD, Platform Infrastructure
**Activity**: 1 host (erectus 192.168.1.201 — manual trigger, rsync workflow)

| Field | Value |
|---|---|
| name | docker-build-agent |
| helmRepo | ⚠️ RECOMMENDED: [Forgejo runner](https://forgejo.org/docs/latest/admin/actions/) or [actions-runner-controller](https://github.com/actions/actions-runner-controller) (GitHub ARC) |
| chart | ⚠️ RECOMMENDED: actions-runner-controller or forgejo-runner |
| chartVersion | ⚠️ RECOMMENDED: confirm on adoption |
| namespace | build-system |
| applicationRepo | ⚠️ RECOMMENDED: internal (co-located with Forgejo when TASK-039 lands) |

**Note**: Currently manual — `rsync src/ → erectus:~/gitopsapi-build/src/` then `docker build` on erectus. Formalising this as a managed application unblocks automated CI: image builds on every merge to main. Depends on Forgejo (TASK-039) for a fully automated pipeline.

---

## Planned Applications

### forgejo

**Description**: Self-hosted Git forge — replaces GitHub dependency. Hosts platform repos, CI/CD pipelines (Forgejo Actions), and Helm chart registry (OCI).
**GitHub**: https://github.com/go-gitea/gitea (Forgejo fork)
**Web**: https://forgejo.org
**Category**: Source Control, Artifact Management, CI/CD
**Activity**: 0 (TASK-039 planned — gitopsdev first)

| Field | Value |
|---|---|
| name | forgejo |
| helmRepo | ⚠️ RECOMMENDED: https://codeberg.org/forgejo-contrib/forgejo-helm (confirm) |
| chart | ⚠️ RECOMMENDED: forgejo |
| chartVersion | ⚠️ RECOMMENDED: latest stable |
| namespace | forgejo |
| applicationRepo | ⚠️ n/a (upstream) |

---

### cloudnativepg

**Description**: PostgreSQL operator — declarative PostgreSQL cluster management in Kubernetes. Replaces standalone postgres deployments (Keycloak, etc.).
**GitHub**: https://github.com/cloudnative-pg/cloudnative-pg
**Web**: https://cloudnative-pg.io
**Category**: Data, Operators
**Activity**: 0 (TASK-041 planned)

| Field | Value |
|---|---|
| name | cloudnativepg |
| helmRepo | ⚠️ RECOMMENDED: https://cloudnative-pg.github.io/charts |
| chart | ⚠️ RECOMMENDED: cloudnative-pg |
| chartVersion | ⚠️ RECOMMENDED: latest stable |
| namespace | cnpg-system |

---

### nexus

**Description**: Artifact repository manager — Maven, npm, PyPI, Docker proxy and hosted repositories.
**GitHub**: https://github.com/sonatype/nexus-public
**Web**: https://www.sonatype.com/products/nexus-repository
**Category**: Artifact Management
**Activity**: 0 (proposed)

| Field | Value |
|---|---|
| name | nexus |
| helmRepo | ⚠️ RECOMMENDED: https://sonatype.github.io/helm3-charts |
| chart | ⚠️ RECOMMENDED: nexus-repository-manager |
| chartVersion | ⚠️ RECOMMENDED: latest stable |
| namespace | nexus |

---

### redis

**Description**: In-memory data store — proposed as inter-agent communication channel to replace file-based messaging between platform agents.
**GitHub**: https://github.com/redis/redis
**Web**: https://redis.io
**Category**: Data, Messaging, AI/ML Infrastructure
**Activity**: 0 (proposed — whileyouweresleeping.md)

| Field | Value |
|---|---|
| name | redis |
| helmRepo | ⚠️ RECOMMENDED: https://charts.bitnami.com/bitnami |
| chart | ⚠️ RECOMMENDED: redis |
| chartVersion | ⚠️ RECOMMENDED: latest stable |
| namespace | redis |

**Note**: Proposal to replace file-based agent messaging (`agents/*/incoming/`) with Redis pub/sub channels. Requires agent framework changes.

---

### nfs-server

**Description**: NFS storage server — shared persistent storage for backups with rsync off-site replication.
**GitHub**: ⚠️ RECOMMENDED: evaluate nfs-server-provisioner or democratic-csi
**Web**: ⚠️ RECOMMENDED: confirm
**Category**: Platform Infrastructure, Storage
**Activity**: 0 (proposed — whileyouweresleeping.md)

| Field | Value |
|---|---|
| name | nfs-server |
| helmRepo | ⚠️ RECOMMENDED: https://kubernetes-sigs.github.io/nfs-subdir-external-provisioner |
| chart | ⚠️ RECOMMENDED: nfs-subdir-external-provisioner |
| chartVersion | ⚠️ RECOMMENDED: latest stable |
| namespace | nfs-system |

---

### everything-ai

**Description**: Consolidated AI/ML platform — migrates LLM inference (Ollama) and vector store (Qdrant) into a unified deployment. Possibly multi-instance (per cluster or per team).
**GitHub**: ⚠️ RECOMMENDED: to be defined (internal chart or upstream) |
**Web**: ⚠️ RECOMMENDED: to be defined
**Category**: AI/ML Infrastructure
**Activity**: 0 (proposed — whileyouweresleeping.md)

| Field | Value |
|---|---|
| name | everything-ai |
| helmRepo | ⚠️ RECOMMENDED: define as internal Helm chart bundling ollama + qdrant |
| chart | ⚠️ RECOMMENDED: everything-ai |
| chartVersion | ⚠️ RECOMMENDED: 0.1.0 |
| namespace | ai |

**Note**: This consolidates the current separate `qdrant` and `ollama` HelmReleases. Could be a values-driven umbrella chart.

---

### telegram-bot (management chatbot)

**Description**: Telegram bot for cluster management — reports cluster status, enables mobile approvals for GitOpsAPI workflows. Additional channel to GitOpsGUI.
**GitHub**: ⚠️ RECOMMENDED: new development (internal)
**Web**: https://telegram.org
**Category**: Platform Management, Notifications
**Activity**: 0 (strategic — demo target, whileyouweresleeping.md)

| Field | Value |
|---|---|
| name | telegram-bot |
| helmRepo | ⚠️ RECOMMENDED: internal chart |
| chart | ⚠️ RECOMMENDED: telegram-bot |
| chartVersion | ⚠️ RECOMMENDED: 0.1.0 |
| namespace | telegram-bot |
| applicationRepo | ⚠️ RECOMMENDED: https://github.com/MoTTTT/telegram-bot (new) |

**Note**: This is a net-new product, not a third-party chart. Requires design and implementation.

---

## Summary

| Application | Category | Status | Clusters |
|---|---|---|---|
| gitopsapi | Platform Mgmt | Deployed | openclaw, gitopsdev |
| gitopsgui | Platform Mgmt | Planned | — |
| keycloak | Security | Deployed (StatefulSet) | cluster09 |
| oauth2-proxy | Security | Configured (inactive) | — |
| harbor | Artifact Mgmt | Deployed | cluster09 |
| qdrant | AI/ML | Deployed | cluster09 |
| ollama | AI/ML | Deployed | cluster09 |
| opensearch stack | Observability | Deployed | cluster09 |
| opensearch-operator | Observability | Deployed | cluster09 |
| prometheus/grafana | Observability | Deployed | cluster09 |
| fluent-bit | Observability | Deployed | cluster09 |
| openclaw | Networking | Deployed | cluster09 |
| wso2 | API Mgmt | Deployed | cluster09 |
| cert-manager | Platform Infra | Deployed | cluster09, management |
| piraeus affinity-ctrl | Platform Infra | Deployed | cluster09 |
| cluster09-docs | Documentation | Deployed | cluster09 |
| podzone-docs | Documentation | Deployed | cluster09 |
| docker-build-agent | CI/CD | Active (manual, erectus) | erectus host |
| forgejo | Source Control | Planned (TASK-039) | — |
| cloudnativepg | Data | Planned (TASK-041) | — |
| nexus | Artifact Mgmt | Proposed | — |
| redis | Messaging | Proposed | — |
| nfs-server | Storage | Proposed | — |
| everything-ai | AI/ML | Proposed | — |
| telegram-bot | Platform Mgmt | Strategic | — |
