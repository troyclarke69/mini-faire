"use client";

type Point = {
  label: string;
  value: number;
};

export function SimpleBarChart({
  title,
  points,
  color = "#2e7d72"
}: {
  title: string;
  points: Point[];
  color?: string;
}) {
  const max = Math.max(...points.map((point) => point.value), 1);

  return (
    <section className="panel p-4">
      <h2 className="mb-4 text-sm font-semibold text-slate-950 dark:text-white">{title}</h2>
      <div className="flex h-56 items-end gap-3">
        {points.map((point) => (
          <div key={point.label} className="flex min-w-12 flex-1 flex-col items-center gap-2">
            <div className="flex h-40 w-full items-end rounded-md bg-slate-100 p-1 dark:bg-slate-900">
              <div
                className="w-full rounded-sm transition-all"
                style={{ height: `${Math.max((point.value / max) * 100, 3)}%`, backgroundColor: color }}
                title={`${point.label}: ${point.value}`}
              />
            </div>
            <span className="max-w-24 truncate text-xs text-slate-500" title={point.label}>
              {point.label}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

