"""KEDA integration: scaler catalog + ScaledObject / ScaledJob / TriggerAuthentication / HTTPScaledObject generators.

This package codifies the reference at docs/keda-reference.md so Kairos can:
  - Browse the scaler catalog (UI + API)
  - Generate ScaledObject + ScaledJob YAML for Workload + scaler choice
  - Generate TriggerAuthentication YAML for any credential pattern
  - Generate Azure Workload Identity bundles (SA + TriggerAuth + az CLI hint)
  - Generate HTTPScaledObject YAML for HTTP services
  - Validate generated YAML against the production-best-practices checklist
"""

from kairos.keda.catalog import SCALERS, ScalerCategory, ScalerSpec, get_scaler
from kairos.keda.generator import (
    AzureWorkloadIdentityBundleSpec,
    HTTPConcurrencyMetric,
    HTTPScaledObjectSpec,
    HTTPScalingMetric,
    JobContainerSpec,
    ScaledJobSpec,
    ScaledObjectSpec,
    TriggerAuthenticationSpec,
    render_azure_workload_identity_bundle,
    render_http_scaled_object,
    render_scaled_job,
    render_scaled_object,
    render_trigger_authentication,
)
from kairos.keda.validator import (
    LintFinding,
    lint_http_scaled_object,
    lint_scaled_object,
    lint_trigger_authentication,
)

__all__ = [
    "SCALERS",
    "AzureWorkloadIdentityBundleSpec",
    "HTTPConcurrencyMetric",
    "HTTPScaledObjectSpec",
    "HTTPScalingMetric",
    "JobContainerSpec",
    "LintFinding",
    "ScaledJobSpec",
    "ScaledObjectSpec",
    "ScalerCategory",
    "ScalerSpec",
    "TriggerAuthenticationSpec",
    "get_scaler",
    "lint_http_scaled_object",
    "lint_scaled_object",
    "lint_trigger_authentication",
    "render_azure_workload_identity_bundle",
    "render_http_scaled_object",
    "render_scaled_job",
    "render_scaled_object",
    "render_trigger_authentication",
]
