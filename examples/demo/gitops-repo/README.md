# Sample GitOps Repo Layout

This is the layout the **GitOps repo** would have on the other side of KAIROS's PRs. Argo CD or Flux would sync this repo into AKS.

```
gitops-repo/
├── apps/
│   ├── payments-api/           ← KAIROS edits deployment.yaml or values.yaml here
│   │   ├── deployment.yaml
│   │   ├── scaledobject.yaml
│   │   └── values.yaml         (if Helm-based)
│   ├── inference-api/
│   ├── event-router/
│   └── billing-svc/
├── policies/                   ← OPA/Rego rules enforced in PR validation
│   └── kairos-invariants.rego
└── .github/workflows/
    └── validate.yml            ← runs kubeconform + conftest on every PR
```

## Resolution rules KAIROS uses

- **Path:** `apps/{workload.name}/` (overridable via `kairos.io/gitops-path` annotation)
- **Files tried (in order):**
  1. `deployment.yaml` — Kustomize-style direct Deployment manifest
  2. `values.yaml` — Helm values file
  3. `statefulset.yaml` — for StatefulSet workloads (requires `allow_statefulset_auto_pr`)

The first file that exists + contains editable fields is modified. Others are left alone.

## Branch + commit naming

Every KAIROS PR:
- Branch: `kairos/{namespace}-{name}-{yyyymmdd-hhmm}`
- Title: `[KAIROS] Scale {kind}/{name} in {namespace}: {action}`
- Body: rendered from `src/kairos/gitops/pr_template.py` (forecast chart link, rationale, LLM advice, reviewer checklist, decision hash)
- Labels: `kairos`, `autoscaling`, `{action}`, `severity:{severity}`
- Reviewers: configured via `KAIROS_GITHUB__REVIEWERS` + workload annotation `kairos.io/reviewers`
