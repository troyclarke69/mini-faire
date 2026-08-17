import { api, money, number, percent } from "@/lib/api";
import { EmptyState } from "@/components/EmptyState";
import { TablePanel } from "@/components/TablePanel";

export async function OrderProfitabilityTable() {
  const rows = await api.orderProfitability();

  return (
    <TablePanel title="Order Profitability">
      {rows.length === 0 ? (
        <EmptyState />
      ) : (
        <table className="min-w-full">
          <thead className="table-head">
            <tr>
              {["Order", "Date", "Retailer", "Product", "Qty", "Net", "Profit", "Margin"].map((heading) => (
                <th key={heading} className="px-3 py-2">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.order_id}>
                <td className="table-cell font-medium">{row.order_id}</td>
                <td className="table-cell">{row.order_date}</td>
                <td className="table-cell">{row.retailer_id}</td>
                <td className="table-cell">{row.product_id}</td>
                <td className="table-cell">{number(row.quantity)}</td>
                <td className="table-cell">{money(row.net_amount)}</td>
                <td className="table-cell">{money(row.estimated_profit)}</td>
                <td className="table-cell">{percent(row.estimated_margin)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </TablePanel>
  );
}

