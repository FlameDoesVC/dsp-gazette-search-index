"use client";

import { useState } from "react";
import { Bidi } from "@/components/Bidi";
import {
  DEFAULT_WORKING_DAYS, estimateNet, type Compensation,
} from "@/lib/compensation";
import { formatMoney } from "@/lib/format";

export function CompensationTable({ comp }: { comp: Compensation }) {
  // The input keeps the raw string so a user can clear it and type a fresh
  // number; only the committed value is clamped to 1..31.
  const [raw, setRaw] = useState(String(DEFAULT_WORKING_DAYS));
  const parsed = Number(raw);
  const days = Number.isFinite(parsed)
    ? Math.min(31, Math.max(1, parsed))
    : DEFAULT_WORKING_DAYS;
  const est = estimateNet(comp, days);

  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold">Pay breakdown</h2>

      <table className="w-full text-sm">
        <tbody>
          <tr className="border-b border-line">
            <td className="py-1.5">Basic salary</td>
            <td className="py-1.5 text-end">
              {comp.basic_salary != null
                ? formatMoney(comp.basic_salary, comp.currency)
                : "-"}
            </td>
          </tr>
          {comp.allowances.map((a, i) => (
            <tr key={i} className="border-b border-line">
              <td className="py-1.5">
                <Bidi as="span" text={a.label_raw || a.kind} />
                {a.basis === "per_day" && (
                  <span className="ms-1 text-xs text-muted">per day</span>
                )}
                {a.basis === "percent_of_basic" && (
                  <span className="ms-1 text-xs text-muted">% of basic</span>
                )}
              </td>
              <td className="py-1.5 text-end">
                {a.amount != null ? a.amount.toLocaleString() : "-"}
              </td>
            </tr>
          ))}
          {comp.pension_applies && est && (
            <tr className="border-b border-line text-muted">
              <td className="py-1.5">
                Pension ({Math.round((comp.pension_rate || 0.07) * 100)}% of basic)
              </td>
              <td className="py-1.5 text-end">
                {Math.round(
                  -(comp.basic_salary ?? 0) * (comp.pension_rate || 0.07)
                ).toLocaleString()}
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <label className="flex items-center gap-2 text-sm">
        Working days per month
        <input
          type="number"
          min={1}
          max={31}
          value={raw}
          onChange={(e) => setRaw(e.target.value)}
          className="w-16 rounded border border-line px-1.5 py-0.5"
        />
      </label>

      {est ? (
        <p data-testid="net-total" className="text-base font-semibold">
          {est.is_floor ? "At least " : ""}~{formatMoney(Math.round(est.value),
                                                         comp.currency)}
          <span className="ms-2 text-xs font-normal text-muted">
            estimated take-home, {days} working days
          </span>
        </p>
      ) : (
        <p className="text-sm text-muted">
          Not enough detail in the listing to estimate take-home pay.
        </p>
      )}
    </section>
  );
}
