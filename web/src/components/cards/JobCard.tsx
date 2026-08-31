"use client";

import { Bidi } from "@/components/Bidi";
import type { ResultOut } from "@/lib/api";
import { formatApproxMoney, formatDate, formatDateDv } from "@/lib/format";
import { isDhivehi } from "@/lib/script";
import { UI_DV } from "@/lib/uiLabels";
import { ApplyBlock } from "@/components/detail/ApplyBlock";
import { CompensationTable } from "@/components/detail/CompensationTable";
import { CardShell } from "./CardShell";
import { ProfileNote, SimilarCount } from "./EntityMeta";

const DEADLINE_TONE: Record<string, string> = {
  open: "text-base-content/60",
  closing_soon: "text-amber-700",
  closed: "text-base-content/60 line-through",
};

export function JobCard({ result }: { result: ResultOut }) {
  const c = result.card as Record<string, never>;
  const est = c.net_estimate as { value: number; is_floor: boolean } | null;
  // `result.title` is already resolved to the response language server-side
  // (spec 9, with `translated` flagging a fallback); `role` is enrichment's
  // own free-text extraction and carries no language guarantee at all, so it
  // only overrides the title when it happens to be in the same script --
  // otherwise a Dhivehi query would surface an English card.role and silently
  // defeat the whole response-language resolution.
  const dv = isDhivehi(result.title);
  const role = c.role as string | undefined;
  const title = (role && isDhivehi(role) === dv) ? role : result.title;
  const currency = (c.compensation as { currency?: string } | undefined)?.currency || "MVR";
  const qualifications = (c.qualifications as string[]) ?? [];
  const qualificationsDv = (c.qualifications_dv as string[]) ?? [];
  const requiredDocuments = (c.required_documents as string[]) ?? [];
  const requiredDocumentsDv = (c.required_documents_dv as string[]) ?? [];
  const applyMethods = (c.apply_methods as never as {
    kind: string; value: string; label_en?: string; label_dv?: string;
  }[]) ?? [];
  const hasDetails = qualifications.length > 0 || requiredDocuments.length > 0 ||
    !!c.compensation || applyMethods.length > 0;
  const employer = (dv && (c.employer_dv as string)) || (c.employer as string);

  return (
    <CardShell
      result={result}
      title={title}
      summary={hasDetails ? undefined : (result.summary || undefined)}
      highlightDir={est && dv ? "rtl" : "ltr"}
      highlight={
        est ? (
          // Take-home leads when there is one to show: it is the figure a
          // candidate actually cares about, and the stated basic salary
          // behind it stays visible in Details rather than the headline.
          // The ~ is the only assumption-honesty this needs -- an estimate
          // is never rendered as a stated fact.
          <span data-testid="net-estimate" dir={dv ? "rtl" : "ltr"} lang={dv ? "dv" : "en"}>
            {dv
              ? `${formatApproxMoney(est.value, currency, true)} ${UI_DV.perMonth}`
              : `${formatApproxMoney(est.value, currency)} / month`}
          </span>
        ) : (
          /* Rendered verbatim. Already resolved server-side to one of three
              strings; the frontend never interprets a null into 'Negotiable'. */
          <>{c.salary_display as string}</>
        )
      }
    >
      <Bidi as="p" text={employer} className="text-sm text-base-content/60" />
      {hasDetails && (
        <div className="mt-3 space-y-4">
          {qualifications.length > 0 && (
            <div>
              <Bidi as="h3" text={dv ? UI_DV.qualifications : "Qualifications"}
                    className="text-sm font-semibold" />
              <ul className="mt-1 list-disc space-y-0.5 ps-4 text-sm text-base-content/60">
                {qualifications.map((q, i) => (
                  <Bidi as="li" key={q} text={(dv && qualificationsDv[i]) || q} />
                ))}
              </ul>
            </div>
          )}
          {(requiredDocuments.length > 0 || c.compensation) && (
            <div
              dir={dv ? "rtl" : "ltr"}
              className={
                requiredDocuments.length > 0 && c.compensation
                  // Both: side by side on desktop. Only one: full width, never
                  // a lone column beside an empty one. `dir` on the grid
                  // itself, not just its children, is what actually swaps
                  // which side each column lands on for a Dhivehi document --
                  // grid-column order follows DOM order under the container's
                  // own direction, not each child's individual dir.
                  ? "space-y-4 lg:grid lg:grid-cols-2 lg:gap-6 lg:space-y-0"
                  : ""
              }
            >
              {requiredDocuments.length > 0 && (
                <div>
                  <Bidi as="h3" text={dv ? UI_DV.requiredDocuments : "Required documents"}
                        className="text-sm font-semibold" />
                  <ul className="mt-1 list-disc space-y-0.5 ps-4 text-sm text-base-content/60">
                    {requiredDocuments.map((d, i) => (
                      <Bidi as="li" key={d} text={(dv && requiredDocumentsDv[i]) || d} />
                    ))}
                  </ul>
                </div>
              )}
              {c.compensation ? (
                <CompensationTable comp={c.compensation as never} headingLevel="h3" dv={dv} />
              ) : null}
            </div>
          )}
          <ApplyBlock methods={applyMethods} headingLevel="h3" dv={dv} />
        </div>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-base-content/60">
        {/* `grade` is dropped here: enrichment sometimes confuses an
            applicant's required MNQF level with the position's own grade,
            and the two are indistinguishable once both land in this field. */}
        <Bidi as="span" text={dv ? (c.position_type_label_dv as string) : (c.position_type_label_en as string)} />
        <Bidi as="span" text={c.location as string} />
        {c.deadline ? (
          <span
            data-testid="deadline"
            dir={dv ? "rtl" : "ltr"}
            lang={dv ? "dv" : "en"}
            className={DEADLINE_TONE[(c.deadline_state as string) ?? "open"]}
          >
            {dv
              ? `ސުންގަޑި: ${formatDateDv(c.deadline as string)}`
              : `Closes ${formatDate(c.deadline as string)}`}
          </span>
        ) : null}
        {c.detail_source === "attachment" ? (
          <Bidi as="span" className="badge badge-sm"
                text={dv ? UI_DV.detailsFromAttachment : "Details from attached document"} />
        ) : null}
      </div>
      <SimilarCount count={c.listing_count as number | undefined} />
      <ProfileNote
        tier={c.profile_tier as string | undefined}
        inferredCount={c.inferred_count as number | undefined}
        fieldCount={c.field_count as number | undefined}
      />
    </CardShell>
  );
}
