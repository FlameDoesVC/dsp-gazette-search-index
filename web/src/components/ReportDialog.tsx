"use client";

import { useState } from "react";
import { postReport } from "@/lib/api";

const REASONS = [
  ["stale", "The information is out of date"],
  ["wrong_details", "The details are wrong"],
  ["dead_link", "The link is broken"],
  ["spam", "This is spam or a scam"],
  ["other", "Something else"],
] as const;

/**
 * A <dialog> with the five reasons, an optional note, and a fixed
 * confirmation message regardless of outcome -- the endpoint always returns
 * 202 and the UI must not leak whether the report was new (spec 9).
 */
export function ReportDialog({ documentId }: { documentId: number }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<string>("stale");
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

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="text-xs text-muted underline underline-offset-2"
      >
        Report a problem
      </button>
    );
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4">
      <div role="dialog" aria-modal="true" className="w-full max-w-md rounded-lg bg-bg p-4 shadow-lg">
        <h2 className="text-base font-semibold">Report a problem</h2>
        {done ? (
          <p className="mt-3 text-sm text-muted">
            Thanks for letting us know. The report has been recorded.
          </p>
        ) : (
          <>
            <div className="mt-3 space-y-1.5">
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
              className="mt-3 w-full rounded border border-line px-2 py-1.5 text-sm"
            />
            <div className="mt-3 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="rounded border border-line px-3 py-1.5 text-sm"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={submit}
                className="rounded bg-accent px-3 py-1.5 text-sm text-white"
              >
                Submit report
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
