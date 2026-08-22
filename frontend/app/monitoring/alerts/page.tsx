import { AlertsFeed } from "@/components/monitoring/AlertsFeed";
import { MonitoringLiveBar } from "@/components/monitoring/MonitoringLiveBar";
import { MonitoringTabs } from "@/components/monitoring/MonitoringTabs";
import { PageHeader } from "@/components/PageHeader";

export default function MonitoringAlertsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Alerts"
        subtitle="Dispatched alerts across anomalies, schema drift, threshold breaches, and pipeline failures - Slack, webhook, and console channels."
      />
      <MonitoringTabs />
      <MonitoringLiveBar />
      <AlertsFeed />
    </div>
  );
}
