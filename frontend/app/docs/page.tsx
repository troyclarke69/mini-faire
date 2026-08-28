import { Download, Github } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";

// A static asset under public/, not an API-fetched page - the master guide is
// a point-in-time export of README.md / governance/lineage.md /
// diagrams/architecture.md (see MASTER_GUIDE.md's own generation note at its
// top), regenerated and re-copied here whenever those source docs change
// meaningfully, rather than rendered live on every request.
const GUIDE_PATH = "/docs/rmap-master-guide.pdf";
const REPO_URL = "https://github.com/troyclarke69/mini-faire";

export default function DocsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Master Technical Guide"
        subtitle="The full RMAP platform reference - overview, every phase's capabilities, the complete API surface, architecture diagrams, and data lineage/governance - compiled into one document."
      />
      <div className="panel flex flex-wrap items-center justify-between gap-4 p-4">
        <div>
          <p className="text-sm font-medium text-slate-700 dark:text-slate-300">RMAP Master Technical Guide (PDF)</p>
          <p className="text-sm text-slate-500">Viewable below, or open it in its own tab to save/print.</p>
        </div>
        <div className="flex items-center gap-3">
          <a
            href={GUIDE_PATH}
            download
            className="inline-flex items-center gap-2 rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 dark:bg-white dark:text-slate-900 dark:hover:bg-slate-200"
          >
            <Download className="h-4 w-4" aria-hidden="true" />
            Download PDF
          </a>
          <a
            href={REPO_URL}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-2 rounded-md border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-900"
          >
            <Github className="h-4 w-4" aria-hidden="true" />
            View source
          </a>
        </div>
      </div>
      <div className="panel overflow-hidden p-0">
        {/* Native browser PDF viewer via <object>, with a fallback link for
            browsers/embedded webviews that can't render PDFs inline (Next.js
            can't know that ahead of time server-side, so the fallback is
            plain markup inside <object>, not a client-side capability check). */}
        <object data={GUIDE_PATH} type="application/pdf" className="h-[80vh] w-full">
          <div className="flex h-[40vh] flex-col items-center justify-center gap-3 p-6 text-center text-sm text-slate-500">
            <p>Your browser can&apos;t preview PDFs inline.</p>
            <a href={GUIDE_PATH} download className="font-medium text-mint underline-offset-2 hover:underline">
              Download the guide instead
            </a>
          </div>
        </object>
      </div>
    </div>
  );
}
