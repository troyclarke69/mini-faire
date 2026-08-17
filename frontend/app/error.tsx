"use client";

export default function Error({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="panel p-6">
      <h1 className="text-lg font-semibold">Something went sideways</h1>
      <p className="mt-2 text-sm text-slate-500">Refresh the view after confirming the FastAPI backend is running.</p>
      <button
        type="button"
        onClick={reset}
        className="mt-4 rounded-md bg-mint px-3 py-2 text-sm font-medium text-white"
      >
        Retry
      </button>
    </div>
  );
}

