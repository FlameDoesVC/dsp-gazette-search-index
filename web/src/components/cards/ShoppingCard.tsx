"use client";

import { Bidi } from "@/components/Bidi";
import { Disclosure } from "@/components/Disclosure";
import { SpecTable } from "@/components/detail/SpecTable";
import type { ResultOut } from "@/lib/api";
import { CardShell } from "./CardShell";
import { ProfileNote, SimilarCount } from "./EntityMeta";

export function ShoppingCard({ result }: { result: ResultOut }) {
  const c = result.card as Record<string, never>;
  const hero = c.hero_image as string | null;

  return (
    <CardShell
      result={result}
      title={(c.title as string) || result.title}
      summary={result.summary || undefined}
      thumbnail={hero}
      highlight={
        <>
          {(c.price_display as string) ?? "Price on request"}
          {c.negotiable ? (
            <span className="ms-1 text-xs font-normal text-base-content/60">negotiable</span>
          ) : null}
        </>
      }
    >
      {(c.spec_chips as string[])?.length ? (
        <div className="mt-1 flex flex-wrap gap-1">
          {(c.spec_chips as string[]).map((s) => (
            <span key={s} className="badge badge-sm">
              {s}
            </span>
          ))}
        </div>
      ) : null}
      <div className="mt-1 flex items-center gap-1.5 text-xs text-base-content/60">
        {c.condition ? (
          <span className="badge badge-sm">{c.condition as string}</span>
        ) : null}
        {c.seller_is_premium ? (
          <span data-testid="premium" title="Premium seller">★</span>
        ) : null}
      </div>
      <Disclosure label="Details">
        <SpecTable specs={((c.specs as never) ?? []) as {
          key_raw: string; value_num?: number | null; value_text?: string; unit?: string;
        }[]} />
        {c.seller_name && (
          <p className="mt-1 text-sm text-base-content/60">Seller: {c.seller_name as string}</p>
        )}
      </Disclosure>
      <SimilarCount count={c.listing_count as number | undefined} />
      <ProfileNote
        tier={c.profile_tier as string | undefined}
        inferredCount={c.inferred_count as number | undefined}
        fieldCount={c.field_count as number | undefined}
      />
    </CardShell>
  );
}
