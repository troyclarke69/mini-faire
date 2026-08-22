import { ProductFilterPanel } from "@/components/filters/ProductFilterPanel";
import { LiveMetaBar } from "@/components/LiveMetaBar";
import { PageHeader } from "@/components/PageHeader";
import { ProductVelocityTable } from "@/components/tables/ProductVelocityTable";
import { TablePanel } from "@/components/TablePanel";
import { api, number, percent } from "@/lib/api";

export default async function ProductsPage() {
  const [velocity, reorder] = await Promise.all([api.productVelocity(), api.productReorderRisk()]);

  return (
    <div className="space-y-6">
      <PageHeader title="Products" subtitle="Product velocity paired with Polars reorder-risk scoring." />
      <LiveMetaBar />
      <ProductFilterPanel rows={velocity} />
      <ProductVelocityTable />
      <TablePanel title="Product Reorder Risk">
        <table className="min-w-full">
          <thead className="table-head">
            <tr>{["Product", "Brand", "Inventory", "Units", "Sell Through", "Risk", "Band"].map((h) => <th key={h} className="px-3 py-2">{h}</th>)}</tr>
          </thead>
          <tbody>
            {reorder.map((row) => (
              <tr key={row.product_id}>
                <td className="table-cell font-medium">{row.product_name}</td>
                <td className="table-cell">{row.brand_id}</td>
                <td className="table-cell">{number(row.inventory_count)}</td>
                <td className="table-cell">{number(row.units_sold)}</td>
                <td className="table-cell">{percent(row.sell_through_rate)}</td>
                <td className="table-cell">{number(row.reorder_risk_score, 1)}</td>
                <td className="table-cell">{row.reorder_risk_band}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TablePanel>
    </div>
  );
}

