import { AnomalyTable } from "@/components/monitoring/AnomalyTable";
import { MonitoringLiveBar } from "@/components/monitoring/MonitoringLiveBar";
import { MonitoringTabs } from "@/components/monitoring/MonitoringTabs";
import { PageHeader } from "@/components/PageHeader";
import { SimpleBarChart } from "@/components/charts/SimpleBarChart";
import { api } from "@/lib/api";

export default async function MonitoringAnomaliesPage() {
  const rows = await api.anomalies();

  const byType = new Map<string, number>();
  for (const row of rows) {
    byType.set(row.anomaly_type, (byType.get(row.anomaly_type) ?? 0) + 1);
  }
  const byTypePoints = Array.from(byType.entries())
    .sort((a, b) => b[1] - a[1])
    .map(([label, value]) => ({ label: label.replace(/_/g, " "), value }));

  const bySeverity = new Map<string, number>();
  for (const row of rows) {
    bySeverity.set(row.severity, (bySeverity.get(row.severity) ?? 0) + 1);
  }
  const severityPoints = ["critical", "warning", "info"]
    .filter((severity) => bySeverity.has(severity))
    .map((severity) => ({ label: severity, value: bySeverity.get(severity) ?? 0 }));

  return (
    <div className="space-y-6">
      <PageHeader title="Anomalies" subtitle="Statistical anomaly detection across GMV, order velocity, inventory, pricing, event lag, retailer health, and ingestion volume." />
      <MonitoringTabs />
      <MonitoringLiveBar />
      {rows.length > 0 ? (
        <section className="grid gap-4 lg:grid-cols-2">
          <SimpleBarChart title="Anomalies by Type" color="#d45d4c" points={byTypePoints} />
          <SimpleBarChart title="Anomalies by Severity" color="#d89b2b" points={severityPoints} />
        </section>
      ) : null}
      <AnomalyTable />
    </div>
  );
}
