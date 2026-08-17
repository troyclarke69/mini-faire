import { LineageGraph } from "@/components/LineageGraph";
import { PageHeader } from "@/components/PageHeader";
import { IngestionRunsTable } from "@/components/tables/IngestionRunsTable";
import { LineageTable } from "@/components/tables/LineageTable";
import { api } from "@/lib/api";

export default async function LineagePage() {
  const edges = await api.lineageEdges();

  return (
    <div className="space-y-6">
      <PageHeader title="Lineage" subtitle="Path-level ingestion edges plus table-form governance metadata." />
      <LineageGraph edges={edges} />
      <LineageTable />
      <IngestionRunsTable />
    </div>
  );
}

