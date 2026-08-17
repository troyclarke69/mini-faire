import { PageHeader } from "@/components/PageHeader";
import { QuarantineViewer } from "@/components/QuarantineViewer";
import { api } from "@/lib/api";

export default async function QuarantinePage() {
  const records = await api.quarantineRecords();

  return (
    <div className="space-y-6">
      <PageHeader title="Quarantine" subtitle="Invalid records, validation errors, and links back to ingestion runs." />
      <QuarantineViewer records={records} />
    </div>
  );
}

