"use client";

import { useId, useState, type ReactNode } from "react";

/**
 * Inline progressive disclosure. The only way this project reveals extra detail.
 *
 * Deliberately not a modal, popover or tooltip. Results here are mixed-script
 * with per-element direction (spec 10), so an overlay would re-solve direction,
 * focus trapping and scroll locking that the page has already solved -- and it
 * would hide the neighbouring results, which is the comparison a result list
 * exists to support.
 *
 * A button plus a labelled region rather than <details>/<summary>: the native
 * marker does not flip with `dir` and cannot be styled consistently.
 */
export function Disclosure({
  label,
  children,
  defaultOpen = false,
}: {
  label: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();

  return (
    <div className="mt-2">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1 text-xs text-accent
                   underline underline-offset-2"
      >
        {label}
        <span aria-hidden="true">{open ? "−" : "+"}</span>
      </button>
      {open && (
        <div id={id} className="mt-2 border-t border-line pt-2">
          {children}
        </div>
      )}
    </div>
  );
}
