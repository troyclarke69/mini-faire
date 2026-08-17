export function TablePanel({
  title,
  children,
  actions
}: {
  title: string;
  children: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <section className="panel overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-3 dark:border-slate-800">
        <h2 className="text-sm font-semibold text-slate-950 dark:text-white">{title}</h2>
        {actions}
      </div>
      <div className="overflow-x-auto">{children}</div>
    </section>
  );
}

