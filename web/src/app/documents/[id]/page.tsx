import { notFound } from "next/navigation";
import { ApiError, getDocument } from "@/lib/api";
import { ApplyBlock } from "@/components/detail/ApplyBlock";
import { CompensationTable } from "@/components/detail/CompensationTable";
import type { Compensation } from "@/lib/compensation";
import { Gallery } from "@/components/detail/Gallery";
import { SpecTable } from "@/components/detail/SpecTable";
import { Bidi } from "@/components/Bidi";
import { ReportDialog } from "@/components/ReportDialog";
import { SourceBadge } from "@/components/SourceBadge";

export default async function DocumentPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let doc: Record<string, unknown>;
  try {
    doc = await getDocument(Number(id));
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  const docType = String(doc.doc_type ?? "");
  const card = (doc.card ?? {}) as Record<string, never>;
  const title = (card.title as string) || (doc.title_en as string) ||
    (doc.title_dv as string) || "Untitled";
  const summary = (card.summary as string) || (doc.summary_en as string) ||
    (doc.summary_dv as string) || "";
  const thumbnails = ((doc.thumbnails ?? []) as string[]).filter(Boolean);

  return (
    <main className="mx-auto max-w-3xl px-4 py-6">
      <div className="flex items-center justify-between gap-2">
        <SourceBadge sourceKey={String(doc.source ?? "")} />
        <a href={String(doc.url ?? "#")} target="_blank" rel="noopener noreferrer"
           className="text-xs text-accent underline">
          View original listing
        </a>
      </div>

      <Bidi as="h1" text={title} className="mt-2 text-2xl font-semibold" />
      <Bidi as="p" text={summary} className="mt-1 text-sm text-muted" />

      {docType === "shopping" && thumbnails.length > 0 && (
        <div className="mt-4">
          <Gallery images={thumbnails} />
        </div>
      )}

      {docType === "job" && (
        <div className="mt-6">
          <CompensationTable comp={(card.compensation as never as Compensation) ?? {}} />
        </div>
      )}

      {docType === "shopping" && (
        <div className="mt-6">
          <SpecTable specs={(doc.specs as never as {
            key_raw: string; value_num?: number; value_text?: string; unit?: string;
          }[]) ?? []} />
        </div>
      )}

      {docType === "job" && (
        <div className="mt-6">
          <ApplyBlock methods={(card.apply_methods as never as {
            kind: string; value: string; label_en?: string; label_dv?: string;
          }[]) ?? []} />
        </div>
      )}

      <div className="mt-8">
        <ReportDialog documentId={Number(id)} />
      </div>
    </main>
  );
}
