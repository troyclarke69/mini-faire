"use client";

export function ProductSelector({
  products,
  value = "",
  onChange
}: {
  products: string[];
  value?: string;
  onChange?: (product: string) => void;
}) {
  return (
    <select
      value={value}
      onChange={(event) => onChange?.(event.target.value)}
      className="h-9 rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
    >
      <option value="">All products</option>
      {products.map((product) => (
        <option key={product} value={product}>
          {product}
        </option>
      ))}
    </select>
  );
}

