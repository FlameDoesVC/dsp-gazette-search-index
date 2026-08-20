"use client";

import { useState } from "react";
import { Bidi } from "@/components/Bidi";
import { Disclosure } from "@/components/Disclosure";
import type { ResultOut } from "@/lib/api";
import { formatApprox, formatDate } from "@/lib/format";
import { ApplyBlock } from "@/components/detail/ApplyBlock";
import { CompensationTable } from "@/components/detail/CompensationTable";
import { CardShell } from "./CardShell";
import { ProfileNote, SimilarCount } from "./EntityMeta";

const APPLY_LABEL: Record<string, string> = {
  form: "Apply via form",
  email: "Apply by email",
  phone: "Apply by phone",
  viber: "Apply on Viber",
  whatsapp: "Apply on WhatsApp",
  portal: "Apply on a portal",
  walk_in: "Apply in person",
  post: "Apply by post",
};

const APPLY_ICON: Record<string, string> = {
  form: "📝", email: "✉️", phone: "📞", viber: "💬",
  whatsapp: "💬", portal: "🌐", walk_in: "🚶", post: "📮",
};

const DEADLINE_TONE: Record<string, string> = {
  open: "text-base-content/60",
  closing_soon: "text-amber-700",
  closed: "text-base-content/60 line-through",
};

export function JobCard({ result }: { result: ResultOut }) {
  const c = result.card as Record<string, never>;
  const [showAssumptions, setShowAssumptions] = useState(false);
  const est = c.net_estimate as
    | { value: number; is_floor: boolean; working_days: number }
    | null;

  return (
    <CardShell
      result={result}
      title={(c.role as string) || result.title}
      summary={result.summary || undefined}
      highlight={
        /* Rendered verbatim. Already resolved server-side to one of three
            strings; the frontend never interprets a null into 'Negotiable'. */
        <>{c.salary_display as string}</>
      }
    >
      <Bidi as="p" text={c.employer as string} className="text-sm text-base-content/60" />
      {est && (
        <p data-testid="net-estimate" className="mt-0.5 text-xs text-base-content/60">
          {est.is_floor ? "at least " : ""}
          {formatApprox(est.value)} take-home{" "}
          <button
            type="button"
            onClick={() => setShowAssumptions((v) => !v)}
            className="underline underline-offset-2"
            aria-expanded={showAssumptions}
          >
            assumptions
          </button>
        </p>
      )}
      {showAssumptions && est && (
        <p className="mt-1 rounded bg-base-200 px-2 py-1 text-xs text-base-content/60">
          Estimated from the stated line items over {est.working_days} working days,
          less 7% pension on basic salary. Not a figure the employer stated.
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-base-content/60">
        {c.grade ? <span>{c.grade as string}</span> : null}
        {c.position_type ? <span>{c.position_type as string}</span> : null}
        <Bidi as="span" text={c.location as string} />
        {c.deadline ? (
          <span
            data-testid="deadline"
            className={DEADLINE_TONE[(c.deadline_state as string) ?? "open"]}
          >
            Closes {formatDate(c.deadline as string)}
          </span>
        ) : null}
        {c.detail_source === "attachment" ? (
          <span className="badge badge-sm">
            Details from attached document
          </span>
        ) : null}
      </div>
      {(c.apply_kinds as string[])?.length ? (
        <div className="mt-2 flex gap-1.5">
          {(c.apply_kinds as string[]).map((k) => (
            <span
              key={k}
              aria-label={APPLY_LABEL[k] ?? k}
              title={APPLY_LABEL[k] ?? k}
              className="grid h-6 w-6 place-items-center rounded bg-base-200 text-xs"
            >
              {APPLY_ICON[k] ?? "•"}
            </span>
          ))}
        </div>
      ) : null}
      <Disclosure label="Details">
        {(c.qualifications as string[])?.length > 0 && (
          <div className="mb-2">
            <h4 className="text-xs font-semibold">Qualifications</h4>
            <ul className="mt-1 space-y-0.5 text-sm text-base-content/60">
              {(c.qualifications as string[]).map((q) => (
                <li key={q}>{q}</li>
              ))}
            </ul>
          </div>
        )}
        {(c.required_documents as string[])?.length > 0 && (
          <div className="mb-2">
            <h4 className="text-xs font-semibold">Required documents</h4>
            <ul className="mt-1 space-y-0.5 text-sm text-base-content/60">
              {(c.required_documents as string[]).map((d) => (
                <li key={d}>{d}</li>
              ))}
            </ul>
          </div>
        )}
        {c.compensation ? (
          <CompensationTable comp={c.compensation as never} />
        ) : null}
        <ApplyBlock methods={(c.apply_methods as never as {
          kind: string; value: string; label_en?: string; label_dv?: string;
        }[]) ?? []} />
      </Disclosure>
      <SimilarCount count={c.listing_count as number | undefined} />
      <ProfileNote
        tier={c.profile_tier as string | undefined}
        inferredCount={c.inferred_count as number | undefined}
        fieldCount={c.field_count as number | undefined}
      />
    </CardShell>
  );
}
