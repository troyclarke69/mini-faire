import { MLLiveBar } from "@/components/ml/MLLiveBar";
import { MLTabs } from "@/components/ml/MLTabs";
import { ModelRegistryTable } from "@/components/ml/ModelRegistryTable";
import { PageHeader } from "@/components/PageHeader";

export default function MLModelsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Model Registry"
        subtitle="Every trained model version (ml/registry.py) - metadata, evaluation metrics, and promotion status. A version is promoted to active only if it beats the currently active version's eval metric by config/ml.yaml's minimum improvement threshold, and a version that fails its post-activation sanity check is rolled back automatically."
      />
      <MLTabs />
      <MLLiveBar />
      <ModelRegistryTable />
    </div>
  );
}
