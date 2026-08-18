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
  children,
}: {
  result: ResultOut;
  title: string;
  summary?: string;
  thumbnail?: string | null;
  /** The one number worth leading with (price / salary / rent). */
  highlight?: ReactNode;
  children?: ReactNode;
}) {
  const card = result.card as Record<string, never>;
  const sourceUrl = (card.external_url as string) || result.url;

  return (
    <article className="card">
      <div className="flex items-center justify-between gap-2 text-xs">
        <SourceBadge sourceKey={result.source} size="sm" />
        <a
          href={sourceUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="text-muted hover:text-accent hover:underline"
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
            <div className="mt-0.5 text-[15px] font-medium">{highlight}</div>
          ) : null}
          {summary ? (
            <Bidi
              as="p"
              text={summary}
              className="mt-0.5 line-clamp-2 text-sm text-muted"
            />
          ) : null}
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
