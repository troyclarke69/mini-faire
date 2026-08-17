import { OrdersFilterPanel } from "@/components/filters/OrdersFilterPanel";
import { PageHeader } from "@/components/PageHeader";
import { OrderProfitabilityTable } from "@/components/tables/OrderProfitabilityTable";
import { api } from "@/lib/api";

export default async function OrdersPage() {
  const [orders, lag] = await Promise.all([api.orderProfitability(), api.eventLagSummary()]);

  return (
    <div className="space-y-6">
      <PageHeader title="Orders" subtitle="Order margin, profitability, and event lag diagnostics." />
      <OrdersFilterPanel orders={orders} lag={lag} />
      <OrderProfitabilityTable />
    </div>
  );
}

