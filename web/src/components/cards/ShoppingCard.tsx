"use client";

import { Bidi } from "@/components/Bidi";
import { SourceBadge } from "@/components/SourceBadge";
import type { ResultOut } from "@/lib/api";

export function ShoppingCard({ result }: { result: ResultOut }) {
  const c = result.card as Record<string, never>;
  const hero = c.hero_image as string | null;

  return (
    <article className="card flex flex-col gap-2">
      {hero ? (
        <img
          src={hero}
          alt=""
          loading="lazy"
          className="aspect-square w-full rounded-md object-cover"
        />
      ) : (
        <div className="grid aspect-square w-full place-items-center rounded-md
                        bg-chip text-xs text-muted">
          No photo
        </div>
      )}

      <Bidi
        as="h3"
        text={(c.title as string) || result.title}
        className="line-clamp-2 text-sm font-medium"
      />

      <p className="text-[15px] font-semibold">
        {(c.price_display as string) ?? "Price on request"}
        {c.negotiable ? (
          <span className="ms-1 text-xs font-normal text-muted">negotiable</span>
        ) : null}
      </p>

      {(c.spec_chips as string[])?.length ? (
        <div className="flex flex-wrap gap-1">
          {(c.spec_chips as string[]).map((s) => (
            <span key={s} className="rounded bg-chip px-1.5 py-0.5 text-[11px]">
              {s}
            </span>
          ))}
        </div>
      ) : null}

      <div className="flex items-center justify-between gap-2 text-xs text-muted">
        <span className="flex items-center gap-1.5">
          {c.condition ? (
            <span className="rounded bg-chip px-1.5 py-0.5">
              {c.condition as string}
            </span>
          ) : null}
          {c.seller_is_premium ? (
            <span data-testid="premium" title="Premium seller">★</span>
          ) : null}
        </span>
        <SourceBadge sourceKey={result.source} size="sm" />
      </div>
    </article>
  );
}
