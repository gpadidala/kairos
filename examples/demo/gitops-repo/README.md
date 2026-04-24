# Sample GitOps Repo Layout

This is the layout the **GitOps repo** would have on the other side of PCAP's PRs. Argo CD or Flux would sync this repo into AKS.

```
gitops-repo/
├── apps/
│   ├── payments-api/           ← PCAP edits deployment.yaml or values.yaml here
│   │   ├── deployment.yaml
│   │   ├── scaledobject.yaml
│   │   └── values.yaml         (if Helm-based)
│   ├── inference-api/
│   ├── event-router/
│   └── billing-svc/
├── policies/                   ← OPA/Rego rules enforced in PR validation
│   └── pcap-invariants.rego
└── .github/workflows/
    └── validate.yml            ← runs kubeconform + conftest on every PR
```

## Resolution rules PCAP uses

- **Path:** `apps/{workload.name}/` (overridable via `pcap.io/gitops-path` annotation)
- **Files tried (in order):**
  1. `deployment.yaml` — Kustomize-style direct Deployment manifest
  2. `values.yaml` — Helm values file
  3. `statefulset.yaml` — for StatefulSet workloads (requires `allow_statefulset_auto_pr`)

The first file that exists + contains editable fields is modified. Others are left alone.

## Branch + commit naming

Every PCAP PR:
- Branch: `pcap/{namespace}-{name}-{yyyymmdd-hhmm}`
- Title: `[PCAP] Scale {kind}/{name} in {namespace}: {action}`
- Body: rendered from `src/pcap/gitops/pr_template.py` (forecast chart link, rationale, LLM advice, reviewer checklist, decision hash)
- Labels: `pcap`, `autoscaling`, `{action}`, `severity:{severity}`
- Reviewers: configured via `PCAP_GITHUB__REVIEWERS` + workload annotation `pcap.io/reviewers`
