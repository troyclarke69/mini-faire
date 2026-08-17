export function EmptyState({ label = "No rows returned" }: { label?: string }) {
  return <div className="p-6 text-sm text-slate-500 dark:text-slate-400">{label}</div>;
}

