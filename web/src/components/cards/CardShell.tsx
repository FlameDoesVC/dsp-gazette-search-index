"use client";

import type { ReactNode } from "react";
import { Bidi } from "@/components/Bidi";
import { SourceBadge } from "@/components/SourceBadge";
import type { ResultOut } from "@/lib/api";

/**
 * The shared result card shell, Google-news style: a source-attribution row
 * (icon + name + a quiet link to the source), then a small thumbnail beside
 * the title, summary and type-specific detail. Images are deliberately small
 * -- the old full-width square image drowned the All tab.
 */
export function CardShell({
  result,
  title,
  summary,
  thumbnail,
  highlight,
  highlightDir,
  children,
}: {
  result: ResultOut;
  title: string;
  summary?: string;
  thumbnail?: string | null;
  /** The one number worth leading with (price / salary / rent). */
  highlight?: ReactNode;
  /** `highlight` is mixed JSX (a money figure plus a word), not a plain
   * string, so it can't go through Bidi's own script detection -- the
   * caller already knows which language it built the node in, so it passes
   * the direction directly. Without this the wrapping div stays LTR by
   * default even when the span inside it sets its own `dir="rtl"`. That
   * inner dir fixes Thaana shaping but not alignment: `text-align: start`
   * resolves against whichever element actually controls this content's
   * alignment, which is this div, not the span (spec 10). */
  highlightDir?: "rtl" | "ltr";
  children?: ReactNode;
}) {
  const card = result.card as Record<string, never>;
  const sourceUrl = (card.external_url as string) || result.url;

  return (
    <article className="card card-border bg-base-100 p-4">
      <div className="flex items-center justify-between gap-2 text-xs">
        <SourceBadge sourceKey={result.source} size="sm" />
        <a
          href={sourceUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="text-base-content/60 hover:text-primary hover:underline"
        >
          View original <span aria-hidden="true">↗</span>
        </a>
      </div>

      <div className="mt-2 flex gap-3">
        <div className="min-w-0 flex-1">
          <Bidi
            as="h3"
            text={title}
            className="line-clamp-2 text-[15px] font-semibold"
          />
          {highlight ? (
            <div className="mt-0.5 text-[15px] font-medium" dir={highlightDir}>
              {highlight}
            </div>
          ) : null}
          {summary ? (
            <Bidi
              as="p"
              text={summary}
              className="mt-0.5 line-clamp-2 text-sm text-base-content/60"
            />
          ) : null}
          {result.translated && (
            // Spec 9: title/summary fell back to the other language because
            // this listing has nothing in the one the query was answered in.
            // Silent fallback would read as "wrong language" rather than
            // "this is all there is".
            <span className="badge badge-sm mt-1">Translated</span>
          )}
          {children}
        </div>
        {thumbnail ? (
          <img
            src={thumbnail}
            alt=""
            loading="lazy"
            className="h-20 w-28 shrink-0 rounded-md object-cover"
          />
        ) : null}
      </div>
    </article>
  );
}
