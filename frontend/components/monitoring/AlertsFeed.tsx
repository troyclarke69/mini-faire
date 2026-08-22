import { TablePanel } from "@/components/TablePanel";
import { api, severityBadgeClasses } from "@/lib/api";
import type { AlertEvent } from "@/lib/types";

type ChannelResult = { ok: boolean; detail: string };

function parseChannels(raw: string): Record<string, ChannelResult> {
  try {
    const parsed = JSON.parse(raw);
    return typeof parsed === "object" && parsed !== null ? parsed : {};
  } catch {
    return {};
  }
}

export async function AlertsFeed({ limit }: { limit?: number } = {}) {
  const rows = await api.alerts();
  const visible = limit ? rows.slice(0, limit) : rows;

  return (
    <TablePanel title="Alerts" actions={<span className="text-xs text-slate-500">{rows.length} total</span>}>
      {visible.length === 0 ? (
        <div className="p-6 text-sm text-slate-500 dark:text-slate-400">No alerts dispatched yet.</div>
      ) : (
        <div className="divide-y divide-slate-200 dark:divide-slate-800">
          {visible.map((alert: AlertEvent) => {
            const channels = parseChannels(alert.dispatched_channels);
            const channelNames = Object.keys(channels).filter((name) => !name.startsWith("_"));
            return (
              <article key={alert.alert_id} className="flex flex-wrap items-start justify-between gap-3 p-4">
                <div className="min-w-0 flex-1">
                  <div className="mb-1 flex flex-wrap items-center gap-2">
                    <span className={severityBadgeClasses(alert.severity)}>{alert.severity}</span>
                    <span className="text-sm font-semibold text-slate-950 dark:text-white">
                      {alert.alert_type.replace(/_/g, " ")}
                    </span>
                    <span className="text-xs text-slate-500">{alert.entity}</span>
                  </div>
                  <p className="text-sm text-slate-700 dark:text-slate-300">{alert.message}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-slate-500">
                    <span title={alert.created_at}>{alert.created_at}</span>
                    {alert.lineage_ref ? <span>lineage: {alert.lineage_ref}</span> : null}
                    <a href={alert.dashboard_url} className="text-mint hover:underline">
                      dashboard link
                    </a>
                    {channelNames.length > 0 ? (
                      <span>
                        sent via{" "}
                        {channelNames
                          .map((name) => `${name}${channels[name]?.ok ? "" : " (failed)"}`)
                          .join(", ")}
                      </span>
                    ) : null}
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </TablePanel>
  );
}
