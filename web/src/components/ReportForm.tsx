"use client";

import { useState } from "react";
import { postReport, type ReportIn } from "@/lib/api";
import { Disclosure } from "@/components/Disclosure";

const REASONS = [
  ["stale", "The information is out of date"],
  ["wrong_details", "The details are wrong"],
  ["dead_link", "The link is broken"],
  ["spam", "This is spam or a scam"],
  ["other", "Something else"],
] as const;

/**
 * A Disclosure holding the five reasons, an optional note, and a fixed
 * confirmation message regardless of outcome -- the endpoint always returns
 * 202 and the UI must not leak whether the report was new (spec 9). Inline,
 * never a dialog: overlays are banned by the cross-cutting rule.
 */
export function ReportForm({ documentId }: { documentId: number }) {
  const [reason, setReason] = useState<ReportIn["reason"]>("stale");
  const [note, setNote] = useState("");
  const [done, setDone] = useState(false);

  async function submit() {
    try {
      await postReport(documentId, { reason, note });
    } catch {
      /* swallowed: the user still sees the same confirmation */
    }
    setDone(true);
  }

  return (
    <Disclosure label="Report a problem">
      {done ? (
        <p className="text-sm text-base-content/60">
          Thanks for letting us know. The report has been recorded.
        </p>
      ) : (
        <>
          <div className="space-y-1.5">
            {REASONS.map(([value, label]) => (
              <label key={value} className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="reason"
                  value={value}
                  checked={reason === value}
                  onChange={() => setReason(value)}
                />
                {label}
              </label>
            ))}
          </div>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Anything else we should know? (optional)"
            rows={3}
            className="textarea textarea-sm mt-3 w-full"
          />
          <div className="mt-3 flex justify-end">
            <button
              type="button"
              onClick={submit}
              className="btn btn-sm btn-primary"
            >
              Submit report
            </button>
          </div>
        </>
      )}
    </Disclosure>
  );
}
