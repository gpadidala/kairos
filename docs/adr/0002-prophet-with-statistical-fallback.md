# ADR-0002 — Prophet as primary forecaster, statistical fallback always available

**Status:** Accepted · 2026-04-23

## Context
48-hour horizon forecasts for CPU/memory must be:
1. Reasonable out of the box (minimum configuration).
2. Robust to short series (cold-start workloads, <14 days history).
3. Robust to library unavailability (Prophet has a C++ toolchain dependency that breaks some builds).
4. Cheap enough to run every 30 minutes for hundreds of workloads.

Prophet handles daily + weekly seasonality with minimal tuning and produces prediction intervals we can use for confidence scoring. But it fails on short series and its install footprint is fragile.

## Decision
- **Primary:** `ProphetForecaster` with additive model, daily + weekly seasonality.
- **Fallback:** `StatisticalForecaster` — rolling p95 + linear slope projection over the lookback window.
- **Trigger fallback when:**
  - Prophet not importable (graceful degradation)
  - Series has < 14 days of data
  - Prophet `.fit()` raises
  - Prediction interval width > 2× the signal mean (degenerate fit)
- Ensemble selects per-series by backtest MAPE on the last 7 days of history.
- Confidence score derived from prediction interval width and historical MAPE, normalized to [0, 1].

## Consequences
- KAIROS keeps functioning when Prophet is unavailable — the statistical fallback is a first-class citizen, not a stub.
- Unit tests assert both paths produce `Forecast` objects with the contract intact.
- Operators can force `ForecastingSettings.use_prophet_if_available=false` to run statistical-only in resource-constrained environments.
- Future work (ADR not yet written): add LSTM/Transformer backends selectable per workload.
