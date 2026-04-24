# ADR-0004 — PCAP never writes to Kubernetes, Grafana production folders, or `main`

**Status:** Accepted · 2026-04-23

## Context
Automated scaling systems that mutate clusters directly (by bypassing GitOps) create three structural problems:
1. **Drift** — cluster state diverges from the GitOps repo; Argo CD/Flux tries to "correct" PCAP's changes, creating oscillation.
2. **Auditability** — changes lack the PR review trail that regulated environments require.
3. **Blast radius** — a bug in PCAP can take production down in seconds, with no pre-merge safety net.

## Decision
PCAP **only** writes via:
- **GitHub Pull Requests** against a configured GitOps repo. Never commits to `main`. Never merges its own PRs.
- **Grafana provisioning API** into a dedicated folder (`PCAP`), never into a production folder unless explicitly overridden.
- **Notifications** (Teams, Slack, Email) — read-only channels for the cluster.

PCAP's Kubernetes ServiceAccount has `get/list/watch` on workload kinds + ConfigMaps + ScaledObjects. **No write verbs on any resource.** RBAC is the defense in depth.

## Consequences
- Reviewers always see a PR before a change lands — the entire human safety net is preserved.
- PCAP cannot cause a cluster outage on its own; worst case, it opens a bad PR that reviewers reject.
- Operators who want tighter loops (e.g. auto-merge on low-risk PRs) add that via their GitHub branch-protection policies — **outside** PCAP.
- CI in the GitOps repo (kubeconform, conftest) provides the second safety layer.
