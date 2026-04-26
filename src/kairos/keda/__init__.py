"""KEDA integration: scaler catalog + ScaledObject / TriggerAuthentication / HTTPScaledObject generators.

This package codifies the reference at docs/keda-reference.md so Kairos can:
  - Browse the scaler catalog (UI + API)
  - Generate ScaledObject YAML for a Workload + scaler choice
  - Generate TriggerAuthentication YAML for a credential pattern
  - Generate HTTPScaledObject YAML for HTTP services
  - Validate ScaledObject YAML against the production-best-practices checklist
"""

from kairos.keda.catalog import SCALERS, ScalerCategory, ScalerSpec, get_scaler
from kairos.keda.generator import (
    HTTPScaledObjectSpec,
    ScaledObjectSpec,
    TriggerAuthenticationSpec,
    render_http_scaled_object,
    render_scaled_object,
    render_trigger_authentication,
)
from kairos.keda.validator import LintFinding, lint_scaled_object

__all__ = [
    "SCALERS",
    "HTTPScaledObjectSpec",
    "LintFinding",
    "ScaledObjectSpec",
    "ScalerCategory",
    "ScalerSpec",
    "TriggerAuthenticationSpec",
    "get_scaler",
    "lint_scaled_object",
    "render_http_scaled_object",
    "render_scaled_object",
    "render_trigger_authentication",
]
