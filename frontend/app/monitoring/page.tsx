import { AlertsFeed } from "@/components/monitoring/AlertsFeed";
import { AnomalyTable } from "@/components/monitoring/AnomalyTable";
import { MonitoringLiveBar } from "@/components/monitoring/MonitoringLiveBar";
import { MonitoringTabs } from "@/components/monitoring/MonitoringTabs";
import { StreamingStatus } from "@/components/monitoring/StreamingStatus";
import { KpiCard } from "@/components/KpiCard";
import { PageHeader } from "@/components/PageHeader";
import { api, number, percent } from "@/lib/api";

export default async function MonitoringOverviewPage() {
  const [health, metrics] = await Promise.all([api.monitoringHealth(), api.systemMetrics()]);

  const latestByName = new Map<string, number>();
  for (const row of [...metrics].sort((a, b) => a.computed_at.localeCompare(b.computed_at))) {
    latestByName.set(row.metric_name, row.metric_value);
  }
  const avgLatencyMs = latestByName.get("ingestion_latency_avg_ms") ?? 0;
  const quarantineRate = latestByName.get("quarantine_rate") ?? 0;
  const eltFailureRate = latestByName.get("elt_failure_rate") ?? 0;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Monitoring"
        subtitle="Anomaly detection, alerting, and reliability metrics across ingestion, ELT, compute, and the real-time streaming layer."
      />
      <MonitoringTabs />
      <MonitoringLiveBar />
      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard label="Anomalies (1h)" value={number(health?.anomalies_last_hour ?? 0)} detail="All severities" />
        <KpiCard label="Critical Anomalies (1h)" value={number(health?.critical_anomalies_last_hour ?? 0)} />
        <KpiCard label="Alerts (1h)" value={number(health?.alerts_last_hour ?? 0)} />
        <KpiCard label="Ingestion Latency" value={`${number(avgLatencyMs, 0)} ms`} detail="Average" />
        <KpiCard label="Quarantine Rate" value={percent(quarantineRate)} />
        <KpiCard label="ELT Failure Rate" value={percent(eltFailureRate)} />
      </section>
      <StreamingStatus />
      <AnomalyTable limit={10} />
      <AlertsFeed limit={10} />
    </div>
  );
}
