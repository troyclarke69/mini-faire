import { ClusterMap } from "@/components/ml/ClusterMap";
import { MLLiveBar } from "@/components/ml/MLLiveBar";
import { MLTabs } from "@/components/ml/MLTabs";
import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { api } from "@/lib/api";

export default async function MLClustersPage() {
  const rows = await api.clusters();
  const retailers = rows.filter((row) => row.entity_type === "retailer");
  const products = rows.filter((row) => row.entity_type === "product");

  return (
    <div className="space-y-6">
      <PageHeader
        title="Clusters"
        subtitle="Retailer and product segmentation (ml/models/clustering.py) - features standardized, reduced to 2D via PCA for this map, then partitioned with KMeans/DBSCAN/GMM and labeled by whichever trait axis each cluster's centroid deviates from the population mean by the most."
      />
      <MLTabs />
      <MLLiveBar />
      {rows.length === 0 ? (
        <EmptyState label="No cluster assignments yet - run `python orchestration/ml_training_flow.py` then `python orchestration/ml_inference_flow.py`." />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <ClusterMap title="Retailer Segments" rows={retailers} />
          <ClusterMap title="Product Segments" rows={products} />
        </div>
      )}
    </div>
  );
}
