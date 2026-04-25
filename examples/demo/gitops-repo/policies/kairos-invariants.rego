package main

# Policies enforced in PR CI. Conftest runs these against every PR.
# These are guardrails that prevent KAIROS from proposing anything unsafe.

deny[msg] {
  input.kind == "Deployment"
  input.spec.replicas > 50
  msg := sprintf("Deployment %q wants %d replicas — refusing >50 without explicit review", [input.metadata.name, input.spec.replicas])
}

deny[msg] {
  input.kind == "Deployment"
  container := input.spec.template.spec.containers[_]
  not container.resources.requests.cpu
  msg := sprintf("Container %q in %q is missing CPU requests", [container.name, input.metadata.name])
}

deny[msg] {
  input.kind == "Deployment"
  container := input.spec.template.spec.containers[_]
  not container.resources.requests.memory
  msg := sprintf("Container %q in %q is missing memory requests", [container.name, input.metadata.name])
}

deny[msg] {
  input.kind == "ScaledObject"
  input.spec.maxReplicaCount < input.spec.minReplicaCount
  msg := sprintf("ScaledObject %q has maxReplicaCount < minReplicaCount", [input.metadata.name])
}
