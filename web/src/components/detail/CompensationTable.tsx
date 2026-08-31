"use client";

import { useState } from "react";
import { Bidi } from "@/components/Bidi";
import {
  DEFAULT_WORKING_DAYS, estimateNet, type Compensation,
} from "@/lib/compensation";
import { formatApproxMoney, formatMoney } from "@/lib/format";
import { dirFor, langFor } from "@/lib/script";
import { UI_DV } from "@/lib/uiLabels";

export function CompensationTable({
  comp, headingLevel: Heading = "h2", dv = false,
}: {
  comp: Compensation;
  /** The detail page nests this under an h1 (h2 is correct there); a result
   * card's own title is already an h3, so it passes "h3" to keep every
   * heading inside a card at the same level. */
  headingLevel?: "h2" | "h3";
  /** Whether the surrounding document is Dhivehi-dominant -- picks
   * `label_dv` over `label_raw` for an allowance name when both exist, and
   * which language the table's own static labels render in. */
  dv?: boolean;
}) {
  // The input keeps the raw string so a user can clear it and type a fresh
  // number; only the committed value is clamped to 1..31.
  const [raw, setRaw] = useState(String(DEFAULT_WORKING_DAYS));
  const parsed = Number(raw);
  const days = Number.isFinite(parsed)
    ? Math.min(31, Math.max(1, parsed))
    : DEFAULT_WORKING_DAYS;
  const est = estimateNet(comp, days);
  const basicSalaryLabel = dv ? UI_DV.basicSalary : "Basic salary";

  return (
    <section>
      <Bidi as={Heading} className="text-sm font-semibold"
            text={dv ? UI_DV.payBreakdown : "Pay breakdown"} />

      {/* No daisyUI `table`/`table-sm` classes: those carry their own cell
          padding that stacked with ours, which is why this used to run
          visibly taller than the plain lists next to it.
          `dir` on the table itself is what swaps the label/amount columns
          for a Dhivehi document -- table column order follows DOM order
          under the table's own direction, same as the grid above; a per-cell
          dir only fixes that cell's own text shaping and alignment, not
          which side of the row it lands on. */}
      <table className="mt-1.5 w-full border-collapse text-sm" dir={dv ? "rtl" : "ltr"}>
        <tbody>
          <tr className="border-b border-base-300">
            {/* `dir`/`lang` go on the cell itself, not just a nested span:
                text-align resolves against the CELL's own direction, so a
                dir="rtl" span nested inside a plain (LTR) td still renders
                left-aligned -- the per-element bidi principle (spec 10)
                requires the direction to sit on whatever element actually
                controls that content's alignment. */}
            <td className="py-1" dir={dirFor(basicSalaryLabel)} lang={langFor(basicSalaryLabel)}>
              {basicSalaryLabel}
            </td>
            <td className="py-1 text-end">
              {comp.basic_salary != null
                ? formatMoney(comp.basic_salary, comp.currency, dv)
                : "-"}
            </td>
          </tr>
          {comp.allowances.map((a, i) => {
            // percent_of_basic stores the raw percentage in `amount` (e.g. 5
            // meaning 5%), not a currency figure -- the subtext carries the
            // percentage itself, and the amount column shows what it comes
            // to against this listing's basic salary.
            const isPercent = a.basis === "percent_of_basic";
            const amount = isPercent
              ? (a.amount != null && comp.basic_salary != null
                  ? (comp.basic_salary * a.amount) / 100
                  : null)
              : a.amount;
            const label = (dv && a.label_dv) || a.label_raw || a.kind;
            return (
              <tr key={i} className="border-b border-base-300">
                <td className="py-1" dir={dirFor(label)} lang={langFor(label)}>
                  {label}
                  {a.basis === "per_day" && (
                    <span className="ms-1 text-xs text-base-content/60">
                      {dv ? UI_DV.perDay : "per day"}
                    </span>
                  )}
                  {isPercent && a.amount != null && (
                    <span className="ms-1 text-xs text-base-content/60">
                      {dv ? UI_DV.ofBasic(a.amount) : `${a.amount}% of basic`}
                    </span>
                  )}
                </td>
                <td className="py-1 text-end">
                  {amount != null ? formatMoney(amount, comp.currency, dv) : "-"}
                </td>
              </tr>
            );
          })}
          {comp.pension_applies && est && (
            <tr className="border-b border-base-300 text-base-content/60">
              <td className="py-1" dir={dv ? "rtl" : "ltr"} lang={dv ? "dv" : "en"}>
                {dv
                  ? UI_DV.pension(Math.round((comp.pension_rate || 0.07) * 100))
                  : `Pension (${Math.round((comp.pension_rate || 0.07) * 100)}% of basic)`}
              </td>
              <td className="py-1 text-end">
                {formatMoney(
                  Math.round(-(comp.basic_salary ?? 0) * (comp.pension_rate || 0.07)),
                  comp.currency, dv
                )}
              </td>
            </tr>
          )}
        </tbody>
        <tfoot>
          {est ? (
            <tr className="border-t-2 border-base-content/20 font-semibold">
              <td className="py-1.5" dir={dv ? "rtl" : "ltr"} lang={dv ? "dv" : "en"}>
                <label className="inline-flex flex-wrap items-center gap-1 font-normal">
                  {dv ? UI_DV.takeHomeForPrefix : "Take-home for"}
                  <input
                    type="number"
                    min={1}
                    max={31}
                    value={raw}
                    onChange={(e) => setRaw(e.target.value)}
                    className="input input-xs w-16 text-center"
                  />
                  {dv ? UI_DV.takeHomeForSuffix : "working days/month"}
                </label>
              </td>
              <td data-testid="net-total" className="py-1.5 text-end">
                {formatApproxMoney(est.value, comp.currency, dv)}
              </td>
            </tr>
          ) : (
            <tr className="border-t-2 border-base-content/20">
              <td colSpan={2} className="py-1.5 text-base-content/60"
                  dir={dv ? "rtl" : "ltr"} lang={dv ? "dv" : "en"}>
                {dv ? UI_DV.notEnoughDetail
                    : "Not enough detail in the listing to estimate take-home pay."}
              </td>
            </tr>
          )}
        </tfoot>
      </table>
    </section>
  );
}
