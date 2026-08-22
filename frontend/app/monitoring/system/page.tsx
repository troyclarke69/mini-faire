import { MonitoringLiveBar } from "@/components/monitoring/MonitoringLiveBar";
import { MonitoringTabs } from "@/components/monitoring/MonitoringTabs";
import { SystemMetricsChart } from "@/components/monitoring/SystemMetricsChart";
import { PageHeader } from "@/components/PageHeader";
import { api } from "@/lib/api";

export default async function MonitoringSystemPage() {
  const rows = await api.systemMetrics();

  return (
    <div className="space-y-6">
      <PageHeader
        title="System Metrics"
        subtitle="Ingestion, ELT, compute, and streaming reliability trends - latency, throughput, failure rates, and lag."
      />
      <MonitoringTabs />
      <MonitoringLiveBar />
      <SystemMetricsChart rows={rows} />
    </div>
  );
}
