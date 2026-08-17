"use client";

export function RetailerSelector({
  retailers,
  value = "",
  onChange
}: {
  retailers: string[];
  value?: string;
  onChange?: (retailer: string) => void;
}) {
  return (
    <select
      value={value}
      onChange={(event) => onChange?.(event.target.value)}
      className="h-9 rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
    >
      <option value="">All retailers</option>
      {retailers.map((retailer) => (
        <option key={retailer} value={retailer}>
          {retailer}
        </option>
      ))}
    </select>
  );
}

