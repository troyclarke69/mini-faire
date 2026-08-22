import { LiveMetaBar } from "@/components/LiveMetaBar";
import { LineageGraphLive } from "@/components/LineageGraphLive";
import { PageHeader } from "@/components/PageHeader";
import { IngestionRunsTable } from "@/components/tables/IngestionRunsTable";
import { LineageTable } from "@/components/tables/LineageTable";
import { api } from "@/lib/api";

export default async function LineagePage() {
  const edges = await api.lineageEdges();

  return (
    <div className="space-y-6">
      <PageHeader title="Lineage" subtitle="Path-level ingestion edges plus table-form governance metadata." />
      <LiveMetaBar />
      <LineageGraphLive edges={edges} />
      <LineageTable />
      <IngestionRunsTable />
    </div>
  );
}

