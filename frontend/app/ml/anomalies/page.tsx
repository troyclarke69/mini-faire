import { AnomalyClassificationTable } from "@/components/ml/AnomalyClassificationTable";
import { MLLiveBar } from "@/components/ml/MLLiveBar";
import { MLTabs } from "@/components/ml/MLTabs";
import { PageHeader } from "@/components/PageHeader";

export default function MLAnomaliesPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Anomaly Classifications"
        subtitle="ML upgrade to Phase 5's rule-based anomaly detection (ml/models/anomaly_classifier.py) - a classifier trained on anomalies.anomaly_events' own rule-derived anomaly_type labels, predicting that same label from an anomaly's numeric signature. Confidence measures how distinguishable the predicted type is, not whether the anomaly is real."
      />
      <MLTabs />
      <MLLiveBar />
      <AnomalyClassificationTable />
    </div>
  );
}
