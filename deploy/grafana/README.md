# Grafana dashboards

Import these dashboards into Grafana with a Prometheus datasource named
`Prometheus` (or remap the datasource during import). They deliberately show
no data rather than fabricate values when a metric is not yet emitted.

- `platform.json`: request rate, failures, p95 TTFT and p95 end-to-end latency.
- `engine.json`: KV-cache utilisation, preemptions, queue depth and TTFT.
- `cost.json`: token throughput and GPU utilisation, suitable for joining with
  provider billing data in a production datasource.

The dashboards use the `inferia_` metric namespace documented in
`docs/platform/interfaces.md` on the spec branch. Scrape `/inf/metrics` via
the existing `deploy/k8s/prometheus.yaml` configuration.
