import { EventLagChart } from "@/components/charts/EventLagChart";
import { GMVChart } from "@/components/charts/GMVChart";
import { ProfitabilityChart } from "@/components/charts/ProfitabilityChart";
import { VelocityChart } from "@/components/charts/VelocityChart";
import { KpiCard } from "@/components/KpiCard";
import { PageHeader } from "@/components/PageHeader";
import { IngestionRunsTable } from "@/components/tables/IngestionRunsTable";
import { ModelRunsTable } from "@/components/tables/ModelRunsTable";
import { OrderProfitabilityTable } from "@/components/tables/OrderProfitabilityTable";
import { ProductVelocityTable } from "@/components/tables/ProductVelocityTable";
import { RetailerDailyTable } from "@/components/tables/RetailerDailyTable";
import { api, money, number, percent } from "@/lib/api";

export default async function DashboardPage() {
  const [retailerDaily, productVelocity, orderProfitability, eventLag, retailerHealth] = await Promise.all([
    api.retailerDaily(),
    api.productVelocity(),
    api.orderProfitability(),
    api.eventLagSummary(),
    api.retailerHealth()
  ]);

  const gmv = retailerDaily.reduce((sum, row) => sum + row.gmv, 0);
  const orders = retailerDaily.reduce((sum, row) => sum + row.order_count, 0);
  const net = orderProfitability.reduce((sum, row) => sum + row.net_amount, 0);
  const profit = orderProfitability.reduce((sum, row) => sum + row.estimated_profit, 0);
  const velocity =
    productVelocity.reduce((sum, row) => sum + (row.inventory_velocity ?? 0), 0) /
    Math.max(productVelocity.length, 1);
  const lag = eventLag[0]?.avg_lag_seconds ?? 0;
  const health = retailerHealth[0]?.retailer_health_score ?? 0;

  return (
    <div className="space-y-6">
      <PageHeader title="Overview Dashboard" subtitle="Marketplace health across ingestion, warehouse metrics, and Polars compute outputs." />
      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard label="Total GMV" value={money(gmv)} detail="Validated orders" />
        <KpiCard label="Total Orders" value={number(orders)} detail="Last loaded snapshot" />
        <KpiCard label="Avg Order Value" value={money(gmv / Math.max(orders, 1))} detail="GMV per order" />
        <KpiCard label="Velocity Score" value={percent(velocity)} detail="Inventory movement" />
        <KpiCard label="Profit Margin" value={percent(profit / Math.max(net, 1))} detail="Estimated margin" />
        <KpiCard label="Event Lag" value={`${number(lag, 1)}s`} detail="Average event lag" />
        <KpiCard label="Retailer Health" value={number(health, 1)} detail="Top retailer score" />
      </section>
      <section className="grid gap-4 lg:grid-cols-2">
        <GMVChart rows={retailerDaily} />
        <VelocityChart rows={productVelocity} />
        <ProfitabilityChart rows={orderProfitability} />
        <EventLagChart rows={eventLag} />
      </section>
      <section className="grid gap-4">
        <RetailerDailyTable />
        <ProductVelocityTable />
        <OrderProfitabilityTable />
        <ModelRunsTable />
        <IngestionRunsTable />
      </section>
    </div>
  );
}

