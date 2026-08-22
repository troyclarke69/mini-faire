import { MLLiveBar } from "@/components/ml/MLLiveBar";
import { MLTabs } from "@/components/ml/MLTabs";
import { RecommendationList } from "@/components/ml/RecommendationList";
import { PageHeader } from "@/components/PageHeader";

export default function MLRecommendationsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Recommendations"
        subtitle="Product and retailer recommendations (ml/models/recommendations.py) - item-item / retailer-retailer similarity from the retailer x product interaction matrix, plus trend-based 'trending in category' and 'retailer likely to grow' signals."
      />
      <MLTabs />
      <MLLiveBar />
      <RecommendationList />
    </div>
  );
}
