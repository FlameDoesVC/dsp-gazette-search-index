"use client";

import { Bidi } from "@/components/Bidi";
import { SourceBadge } from "@/components/SourceBadge";
import type { ResultOut } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { isDhivehi } from "@/lib/script";
import { ProfileNote, SimilarCount } from "./EntityMeta";

/**
 * Four things and nothing else: icon, title, excerpt, link out. Spec 8.4.
 *
 * The whole card is the anchor. There is no detail route for news, and adding
 * one would mean building a reader for content we do not own.
 */
export function NewsCard({ result }: { result: ResultOut }) {
  const c = result.card as Record<string, never>;
  const href = (c.external_url as string) || result.url;
  const dv = isDhivehi((c.title as string) || result.title);

  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="card card-border bg-base-100 block hover:border-primary p-4"
    >
      <div className="flex items-center justify-between gap-2">
        <SourceBadge sourceKey={result.source} size="sm" />
        <span className="text-xs text-base-content/60">
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
        className="mt-0.5 line-clamp-2 text-sm text-base-content/60"
      />

      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 text-xs text-base-content/60">
        <Bidi as="span" text={c.office as string} />
        <Bidi as="span" text={dv ? (c.announcement_type_label_dv as string) : (c.announcement_type_label_en as string)} />
        {(c.attachment_count as number) > 0 ? (
          <span>{c.attachment_count as number} documents</span>
        ) : null}
        {result.translated && <span className="badge badge-sm">Translated</span>}
      </div>
      <SimilarCount count={c.listing_count as number | undefined} />
      <ProfileNote
        tier={c.profile_tier as string | undefined}
        inferredCount={c.inferred_count as number | undefined}
        fieldCount={c.field_count as number | undefined}
      />
    </a>
  );
}
