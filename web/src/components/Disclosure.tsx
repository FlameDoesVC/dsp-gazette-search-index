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
 * marker does not flip with `dir` and cannot be styled consistently. The
 * DaisyUI collapse classes supply the look (full width, whole header
 * clickable, chevron on the trailing edge) while that structure stays.
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
    <div
      className={`collapse collapse-arrow border border-base-300 bg-base-100
                  mt-2 ${open ? "collapse-open" : "collapse-close"}`}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((v) => !v)}
        className="collapse-title min-h-0 py-3 text-sm font-medium
                   w-full text-start cursor-pointer"
      >
        {label}
      </button>
      {open && (
        <div id={id} className="collapse-content text-sm">
          <div className="pb-3">{children}</div>
        </div>
      )}
    </div>
  );
}
