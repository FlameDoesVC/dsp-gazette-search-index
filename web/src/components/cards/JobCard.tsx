"use client";

import { useState } from "react";
import { Bidi } from "@/components/Bidi";
import { SourceBadge } from "@/components/SourceBadge";
import type { ResultOut } from "@/lib/api";
import { formatApprox, formatDate } from "@/lib/format";

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
  open: "text-muted",
  closing_soon: "text-amber-700",
  closed: "text-muted line-through",
};

export function JobCard({ result }: { result: ResultOut }) {
  const c = result.card as Record<string, never>;
  const [showAssumptions, setShowAssumptions] = useState(false);
  const est = c.net_estimate as
    | { value: number; is_floor: boolean; working_days: number }
    | null;

  return (
    <article className="card">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Bidi
            as="h3"
            text={(c.role as string) || result.title}
            className="truncate text-base font-semibold"
          />
          <Bidi as="p" text={c.employer as string} className="text-sm text-muted" />
        </div>
        <SourceBadge sourceKey={result.source} />
      </div>

      <div className="mt-2">
        {/* Rendered verbatim. Already resolved server-side to one of three
            strings; the frontend never interprets a null into 'Negotiable'. */}
        <p className="text-[15px] font-medium">{c.salary_display as string}</p>

        {est && (
          <p data-testid="net-estimate" className="mt-0.5 text-xs text-muted">
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
          <p className="mt-1 rounded bg-chip px-2 py-1 text-xs text-muted">
            Estimated from the stated line items over {est.working_days} working days,
            less 7% pension on basic salary. Not a figure the employer stated.
          </p>
        )}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
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
          <span className="rounded bg-chip px-1.5 py-0.5">
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
              className="grid h-6 w-6 place-items-center rounded bg-chip text-xs"
            >
              {APPLY_ICON[k] ?? "•"}
            </span>
          ))}
        </div>
      ) : null}
    </article>
  );
}
