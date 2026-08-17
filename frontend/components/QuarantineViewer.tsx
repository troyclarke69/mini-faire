"use client";

import { useMemo, useState } from "react";
import type { QuarantineRecord } from "@/lib/types";

export function QuarantineViewer({ records }: { records: QuarantineRecord[] }) {
  const [entity, setEntity] = useState("");
  const [page, setPage] = useState(0);
  const pageSize = 5;
  const entities = Array.from(new Set(records.map((record) => record.entity)));
  const filtered = useMemo(
    () => (entity ? records.filter((record) => record.entity === entity) : records),
    [entity, records]
  );
  const pageRows = filtered.slice(page * pageSize, page * pageSize + pageSize);
  const pageCount = Math.max(Math.ceil(filtered.length / pageSize), 1);

  return (
    <section className="panel overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-3 dark:border-slate-800">
        <h2 className="text-sm font-semibold">Quarantine Records</h2>
        <select
          value={entity}
          onChange={(event) => {
            setEntity(event.target.value);
            setPage(0);
          }}
          className="h-9 rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
        >
          <option value="">All entities</option>
          {entities.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
      </div>
      {pageRows.length === 0 ? (
        <div className="p-6 text-sm text-slate-500">No invalid records found.</div>
      ) : (
        <div className="divide-y divide-slate-200 dark:divide-slate-800">
          {pageRows.map((record) => (
            <article key={`${record.path}-${record.record_index}`} className="p-4">
              <div className="mb-2 flex flex-wrap gap-2 text-xs text-slate-500">
                <span>{record.entity}</span>
                <span>{record.run_id}</span>
                <span>record {record.record_index}</span>
              </div>
              <pre className="max-h-44 overflow-auto rounded-md bg-slate-950 p-3 text-xs text-slate-100">
                {JSON.stringify(record.record, null, 2)}
              </pre>
              <ul className="mt-3 space-y-1 text-sm text-coral">
                {record.errors.map((error, index) => (
                  <li key={`${error.path}-${index}`}>
                    {error.path || "record"}: {error.message}
                  </li>
                ))}
              </ul>
            </article>
          ))}
        </div>
      )}
      <div className="flex items-center justify-end gap-2 border-t border-slate-200 px-4 py-3 dark:border-slate-800">
        <button
          type="button"
          onClick={() => setPage((value) => Math.max(value - 1, 0))}
          className="rounded-md border border-slate-300 px-3 py-1 text-sm disabled:opacity-40 dark:border-slate-700"
          disabled={page === 0}
        >
          Prev
        </button>
        <span className="text-sm text-slate-500">
          {page + 1} / {pageCount}
        </span>
        <button
          type="button"
          onClick={() => setPage((value) => Math.min(value + 1, pageCount - 1))}
          className="rounded-md border border-slate-300 px-3 py-1 text-sm disabled:opacity-40 dark:border-slate-700"
          disabled={page + 1 >= pageCount}
        >
          Next
        </button>
      </div>
    </section>
  );
}

