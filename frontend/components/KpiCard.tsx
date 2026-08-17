export function KpiCard({
  label,
  value,
  detail
}: {
  label: string;
  value: string;
  detail?: string;
}) {
  return (
    <div className="panel p-4">
      <p className="text-sm font-medium text-slate-500 dark:text-slate-400">{label}</p>
      <p className="mt-2 text-2xl font-semibold text-slate-950 dark:text-white">{value}</p>
      {detail ? <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{detail}</p> : null}
    </div>
  );
}

