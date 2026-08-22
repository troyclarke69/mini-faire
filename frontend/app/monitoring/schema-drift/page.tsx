import { MonitoringLiveBar } from "@/components/monitoring/MonitoringLiveBar";
import { MonitoringTabs } from "@/components/monitoring/MonitoringTabs";
import { SchemaDriftTable } from "@/components/monitoring/SchemaDriftTable";
import { PageHeader } from "@/components/PageHeader";

export default function MonitoringSchemaDriftPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Schema Drift"
        subtitle="Missing fields, new fields, type mismatches, enum violations, and timestamp format issues found in quarantined records."
      />
      <MonitoringTabs />
      <MonitoringLiveBar />
      <SchemaDriftTable />
    </div>
  );
}
