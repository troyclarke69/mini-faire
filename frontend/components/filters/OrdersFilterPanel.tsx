"use client";

import { useMemo, useState } from "react";
import type { EventLagSummary, OrderProfitability } from "@/lib/types";
import { EventLagChart } from "@/components/charts/EventLagChart";
import { ProfitabilityChart } from "@/components/charts/ProfitabilityChart";
import { DateRangePicker } from "@/components/filters/DateRangePicker";

export function OrdersFilterPanel({
  orders,
  lag
}: {
  orders: OrderProfitability[];
  lag: EventLagSummary[];
}) {
  const [range, setRange] = useState({ start: "", end: "" });

  const filtered = useMemo(() => {
    return orders.filter((row) => {
      if (range.start && row.order_date < range.start) return false;
      if (range.end && row.order_date > range.end) return false;
      return true;
    });
  }, [orders, range]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <DateRangePicker start={range.start} end={range.end} onChange={setRange} />
        <span className="text-xs text-slate-500 dark:text-slate-400">
          {filtered.length} of {orders.length} orders match the current range
        </span>
      </div>
      <section className="grid gap-4 lg:grid-cols-2">
        <ProfitabilityChart rows={filtered} />
        <EventLagChart rows={lag} />
      </section>
    </div>
  );
}
