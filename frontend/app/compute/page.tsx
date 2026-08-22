import { LiveMetaBar } from "@/components/LiveMetaBar";
import { PageHeader } from "@/components/PageHeader";
import { TablePanel } from "@/components/TablePanel";
import { api, money, number, percent } from "@/lib/api";

export default async function ComputePage() {
  const [health, brands, cohorts, lag, modelRuns] = await Promise.all([
    api.retailerHealth(),
    api.brandContribution(),
    api.retailerCohortRetention(),
    api.eventLagSummary(),
    api.computeModelRuns()
  ]);

  return (
    <div className="space-y-6">
      <PageHeader title="Polars Compute" subtitle="Derived feature-style outputs produced by the Polars compute layer." />
      <LiveMetaBar />
      <TablePanel title="Retailer Health Scores">
        <table className="min-w-full">
          <thead className="table-head">
            <tr>{["Retailer", "Orders", "Net Revenue", "Profit", "Last Order", "Score"].map((h) => <th key={h} className="px-3 py-2">{h}</th>)}</tr>
          </thead>
          <tbody>
            {health.map((row) => (
              <tr key={row.retailer_id}>
                <td className="table-cell font-medium">{row.retailer_id}</td>
                <td className="table-cell">{number(row.order_count)}</td>
                <td className="table-cell">{money(row.net_revenue)}</td>
                <td className="table-cell">{money(row.estimated_profit)}</td>
                <td className="table-cell">{row.last_order_ts}</td>
                <td className="table-cell">{number(row.retailer_health_score, 1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TablePanel>
      <TablePanel title="Brand Contribution">
        <table className="min-w-full">
          <thead className="table-head">
            <tr>{["Brand", "Category", "Orders", "Units", "GMV", "Margin"].map((h) => <th key={h} className="px-3 py-2">{h}</th>)}</tr>
          </thead>
          <tbody>
            {brands.map((row) => (
              <tr key={`${row.brand_id}-${row.product_category}`}>
                <td className="table-cell font-medium">{row.brand_id}</td>
                <td className="table-cell">{row.product_category}</td>
                <td className="table-cell">{number(row.order_count)}</td>
                <td className="table-cell">{number(row.units_sold)}</td>
                <td className="table-cell">{money(row.gmv)}</td>
                <td className="table-cell">{percent(row.estimated_margin)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TablePanel>
      <section className="grid gap-4 lg:grid-cols-2">
        <TablePanel title="Retailer Cohort Retention">
          <table className="min-w-full">
            <thead className="table-head">
              <tr>{["Signup", "Order Month", "Age", "Active", "Revenue"].map((h) => <th key={h} className="px-3 py-2">{h}</th>)}</tr>
            </thead>
            <tbody>
              {cohorts.map((row, index) => (
                <tr key={`${row.signup_month}-${row.order_month}-${index}`}>
                  <td className="table-cell">{row.signup_month}</td>
                  <td className="table-cell">{row.order_month ?? "n/a"}</td>
                  <td className="table-cell">{number(row.cohort_age_months)}</td>
                  <td className="table-cell">{number(row.active_retailers)}</td>
                  <td className="table-cell">{money(row.net_revenue)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TablePanel>
        <TablePanel title="Event Lag Summary">
          <table className="min-w-full">
            <thead className="table-head">
              <tr>{["Event", "Count", "Min", "Average", "Max"].map((h) => <th key={h} className="px-3 py-2">{h}</th>)}</tr>
            </thead>
            <tbody>
              {lag.map((row) => (
                <tr key={row.event_type}>
                  <td className="table-cell font-medium">{row.event_type}</td>
                  <td className="table-cell">{number(row.event_count)}</td>
                  <td className="table-cell">{number(row.min_lag_seconds, 1)}s</td>
                  <td className="table-cell">{number(row.avg_lag_seconds, 1)}s</td>
                  <td className="table-cell">{number(row.max_lag_seconds, 1)}s</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TablePanel>
      </section>
      <TablePanel title="Compute Model Runs">
        <table className="min-w-full">
          <thead className="table-head">
            <tr>{["Model", "Rows", "Columns", "Target", "Status"].map((h) => <th key={h} className="px-3 py-2">{h}</th>)}</tr>
          </thead>
          <tbody>
            {modelRuns.map((row, index) => (
              <tr key={`${row.model_name}-${index}`}>
                <td className="table-cell font-medium">{row.model_name}</td>
                <td className="table-cell">{number(row.row_count)}</td>
                <td className="table-cell">{number(row.column_count)}</td>
                <td className="table-cell">{row.target_table}</td>
                <td className="table-cell">{row.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TablePanel>
    </div>
  );
}

