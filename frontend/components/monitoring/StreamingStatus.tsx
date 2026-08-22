import { TablePanel } from "@/components/TablePanel";
import { api } from "@/lib/api";
import type { ServiceHeartbeat } from "@/lib/types";

const STATUS_STYLE: Record<string, string> = {
  running: "bg-mint/10 text-mint border-mint/30",
  stale: "bg-marigold/10 text-marigold border-marigold/30",
  not_running: "bg-slate-100 text-slate-500 border-slate-300 dark:bg-slate-900 dark:text-slate-400 dark:border-slate-700"
};

const STATUS_LABEL: Record<string, string> = {
  running: "Running",
  stale: "Stale",
  not_running: "Not running"
};

const SERVICE_LABEL: Record<string, string> = {
  stream_generator: "Synthetic Stream Generator",
  mongo_change_stream: "MongoDB Change Stream",
  realtime_flow: "Real-Time Orchestration"
};

function ServiceCard({ name, heartbeat }: { name: string; heartbeat: ServiceHeartbeat | undefined }) {
  const status = heartbeat?.status ?? "not_running";
  return (
    <div className="panel flex items-center justify-between gap-3 p-4">
      <div>
        <p className="text-sm font-medium text-slate-950 dark:text-white">{SERVICE_LABEL[name] ?? name}</p>
        <p className="mt-1 text-xs text-slate-500">
          {heartbeat?.last_heartbeat_at ? `Last heartbeat: ${heartbeat.last_heartbeat_at}` : "Never seen"}
        </p>
      </div>
      <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium ${STATUS_STYLE[status]}`}>
        {STATUS_LABEL[status] ?? status}
      </span>
    </div>
  );
}

export async function StreamingStatus() {
  const services = await api.streamingStatus();

  return (
    <TablePanel title="Streaming Services">
      <div className="grid gap-3 p-4 sm:grid-cols-3">
        <ServiceCard name="stream_generator" heartbeat={services?.stream_generator} />
        <ServiceCard name="mongo_change_stream" heartbeat={services?.mongo_change_stream} />
        <ServiceCard name="realtime_flow" heartbeat={services?.realtime_flow} />
      </div>
    </TablePanel>
  );
}
