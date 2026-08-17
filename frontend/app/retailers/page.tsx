import { RetailerFilterPanel } from "@/components/filters/RetailerFilterPanel";
import { PageHeader } from "@/components/PageHeader";
import { RetailerDailyTable } from "@/components/tables/RetailerDailyTable";
import { api } from "@/lib/api";

export default async function RetailersPage() {
  const rows = await api.retailerDaily();

  return (
    <div className="space-y-6">
      <PageHeader title="Retailers" subtitle="Daily retailer metrics with cached server-rendered tables and client-side controls." />
      <RetailerFilterPanel rows={rows} />
      <RetailerDailyTable />
    </div>
  );
}

