"use client";

import { Bidi } from "@/components/Bidi";
import { SourceBadge } from "@/components/SourceBadge";
import type { ResultOut } from "@/lib/api";
import { formatRelative } from "@/lib/format";

/**
 * Four things and nothing else: icon, title, excerpt, link out. Spec 8.4.
 *
 * The whole card is the anchor. There is no detail route for news, and adding
 * one would mean building a reader for content we do not own.
 */
export function NewsCard({ result }: { result: ResultOut }) {
  const c = result.card as Record<string, never>;
  const href = (c.external_url as string) || result.url;

  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="card block hover:border-accent"
    >
      <div className="flex items-center justify-between gap-2">
        <SourceBadge sourceKey={result.source} size="sm" />
        <span className="text-xs text-muted">
          {formatRelative(c.published_at as string)}
        </span>
      </div>

      <Bidi
        as="h3"
        text={(c.title as string) || result.title}
        className="mt-1 font-semibold"
      />
      <Bidi
        as="p"
        text={(c.summary as string) || result.summary}
        className="mt-0.5 line-clamp-2 text-sm text-muted"
      />

      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 text-xs text-muted">
        <Bidi as="span" text={c.office as string} />
        <Bidi as="span" text={c.announcement_type as string} />
        {(c.attachment_count as number) > 0 ? (
          <span>{c.attachment_count as number} documents</span>
        ) : null}
      </div>
    </a>
  );
}
