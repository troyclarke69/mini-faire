import { AnomalyClassificationTable } from "@/components/ml/AnomalyClassificationTable";
import { MLLiveBar } from "@/components/ml/MLLiveBar";
import { MLTabs } from "@/components/ml/MLTabs";
import { ModelRegistryTable } from "@/components/ml/ModelRegistryTable";
import { RecommendationList } from "@/components/ml/RecommendationList";
import { KpiCard } from "@/components/KpiCard";
import { PageHeader } from "@/components/PageHeader";
import { api, number } from "@/lib/api";

export default async function MLOverviewPage() {
  const [models, forecasts, clusters, recommendations, classifications] = await Promise.all([
    api.mlModels(),
    api.forecasts(),
    api.clusters(),
    api.recommendations(),
    api.anomalyClassifications()
  ]);

  const activeModels = models.filter((model) => model.status === "active").length;

  return (
    <div className="space-y-6">
      <PageHeader
        title="ML"
        subtitle="Forecasting, clustering, recommendations, and ML-upgraded anomaly classification (PHASE6-ML.md). Train with `python orchestration/ml_training_flow.py`, refresh predictions with `python orchestration/ml_inference_flow.py`."
      />
      <MLTabs />
      <MLLiveBar />
      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <KpiCard label="Active Models" value={number(activeModels)} detail={`${models.length} version(s) total`} />
        <KpiCard label="Forecasts" value={number(forecasts.length)} />
        <KpiCard label="Cluster Assignments" value={number(clusters.length)} />
        <KpiCard label="Recommendations" value={number(recommendations.length)} />
        <KpiCard label="Classified Anomalies" value={number(classifications.length)} />
      </section>
      <ModelRegistryTable />
      <RecommendationList limit={5} />
      <AnomalyClassificationTable limit={10} />
    </div>
  );
}
