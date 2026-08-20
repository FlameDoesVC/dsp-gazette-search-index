"use client";

import { useId, useState, type ReactNode } from "react";

/**
 * SPIKE, throwaway. A daisyui collapse that keeps this project's a11y contract.
 *
 * daisyui documents `collapse` primarily on <details>/<summary>, which
 * Disclosure.tsx deliberately avoided: the native marker does not flip with
 * `dir` and cannot be styled consistently. So this uses daisyui's classes on a
 * button plus a labelled region -- the same structure the project already has --
 * to test whether the LOOK can be adopted without giving up the behaviour.
 *
 * Full width, whole header clickable, chevron on the trailing edge. What the
 * spike is checking is whether daisyui's spacing and that chevron use logical
 * properties, because globals.css already warns that `margin-left` on a card
 * that may be RTL is the single most common way this gets broken.
 */
export function DisclosureSpike({
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
      <div id={id} className="collapse-content text-sm" hidden={!open}>
        <div className="pb-3">{children}</div>
      </div>
    </div>
  );
}
