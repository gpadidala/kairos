# PCAP PromQL Query Catalogue

Every query PCAP issues against Mimir is defined **only** in [`src/pcap/collectors/promql_library.py`](../../src/pcap/collectors/promql_library.py). Copy-pasting query strings anywhere else is a bug.

All queries accept these placeholders unless noted:

| Placeholder | Example | Notes |
|---|---|---|
| `$namespace` | `prod` | Kubernetes namespace |
| `$workload` | `payments-api` | Workload name (used in `pod=~"$workload-.*"`) |
| `$rate_window` | `5m` | Window for `rate(...)` / `increase(...)`. Default `5m` |
| `$scaledobject` | `payments-api-scaler` | KEDA `ScaledObject` name (KEDA queries only) |

---

## Base metrics (all runtimes)

### `cpu_usage_cores`
```promql
sum(rate(container_cpu_usage_seconds_total{
  namespace="$namespace",pod=~"$workload-.*",container!="POD",container!=""
}[$rate_window]))
```
CPU in cores (not millicores) averaged over the window. Uses cAdvisor metrics via kubelet.

### `memory_working_set_bytes`
```promql
sum(container_memory_working_set_bytes{
  namespace="$namespace",pod=~"$workload-.*",container!="POD",container!=""
})
```
Working set = RSS + active file cache. This is what the OOM killer sees.

### `replicas`
```promql
sum(kube_deployment_status_replicas_available{...})
 or sum(kube_statefulset_status_replicas_ready{...})
 or sum(kube_daemonset_status_number_ready{...})
```
Number of ready replicas. Uses `or` so one query works across kinds.

### `pod_restarts`
```promql
sum(increase(kube_pod_container_status_restarts_total{
  namespace="$namespace",pod=~"$workload-.*"
}[$rate_window]))
```
Restarts in the window. Input to decision gating — repeated restarts should suppress aggressive scale-down.

---

## JVM (Micrometer / `prometheus` registry)

### `jvm_heap_used`
```promql
sum(jvm_memory_used_bytes{namespace="$namespace",pod=~"$workload-.*",area="heap"})
```
Heap-only; excludes non-heap (Metaspace, code cache).

### `jvm_gc_pause_seconds`
```promql
sum(rate(jvm_gc_pause_seconds_sum{namespace="$namespace",pod=~"$workload-.*"}[$rate_window]))
```
GC pause time per second. High values suggest heap pressure → `VERTICAL_UP`.

### `jvm_threads`
```promql
sum(jvm_threads_live_threads{namespace="$namespace",pod=~"$workload-.*"})
```
Live thread count. Correlates with connection pool saturation.

---

## Python (`prometheus_client` / gunicorn / uvicorn)

### `python_workers`
```promql
sum(python_active_workers{namespace="$namespace",pod=~"$workload-.*"})
```
Active workers (custom metric — emit from your worker pool).

### `python_rss`
```promql
sum(process_resident_memory_bytes{namespace="$namespace",pod=~"$workload-.*"})
```
Default metric from `prometheus_client` — RSS is the right proxy for memory pressure.

---

## Go

### `go_goroutines`
```promql
sum(go_goroutines{namespace="$namespace",pod=~"$workload-.*"})
```
Goroutine count. Sustained growth usually indicates a leak or blocked channel.

### `go_memstats_heap_inuse_bytes`
```promql
sum(go_memstats_heap_inuse_bytes{namespace="$namespace",pod=~"$workload-.*"})
```
In-use heap; better than `alloc` for memory-pressure decisions.

---

## .NET

### `dotnet_gc_heap_size`
```promql
sum(dotnet_total_memory_bytes{namespace="$namespace",pod=~"$workload-.*"})
```
Managed heap — from `prometheus-net.DotNetRuntime`.

### `dotnet_threadpool_thread_count`
```promql
sum(dotnet_threadpool_threads_count{namespace="$namespace",pod=~"$workload-.*"})
```
ThreadPool size. Correlates with ingress concurrency and blocking calls.

---

## KEDA

### `keda_scaler_metrics_value`
```promql
max(keda_scaler_metrics_value{namespace="$namespace",scaledobject="$scaledobject"})
```
Current metric value KEDA sees. A sustained upward trend triggers PCAP's `KEDA_PRESCALE` recommendation.

### `keda_scaler_active`
```promql
max(keda_scaler_active{namespace="$namespace",scaledobject="$scaledobject"})
```
`1` if the scaler has crossed its activation threshold; `0` otherwise.

---

## Scaling these up

All queries aggregate **across pods**. The KEDA and "replicas" queries **expect `$workload` to match a Deployment/StatefulSet/DaemonSet name exactly**. Pod regex is `$workload-.*` — if your pods don't follow that convention, override in the PromQL library rather than here.

Runtime label expectations:
- JVM: requires `area="heap"` label on `jvm_memory_used_bytes` (Micrometer default)
- Python: `process_resident_memory_bytes` from the `prometheus_client` library
- Go: stdlib `expvar`/`runtime` integrated with a Prometheus endpoint
- .NET: requires `prometheus-net.DotNetRuntime` collector enabled
