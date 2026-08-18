"use client";

import { Bidi } from "@/components/Bidi";
import { Disclosure } from "@/components/Disclosure";
import { SourceBadge } from "@/components/SourceBadge";
import type { ResultOut } from "@/lib/api";

export function PropertyCard({ result }: { result: ResultOut }) {
  const c = result.card as Record<string, never>;
  const hero = c.hero_image as string | null;

  return (
    <article className="card flex gap-3">
      {hero ? (
        <div className="relative shrink-0">
          <img
            src={hero}
            alt=""
            loading="lazy"
            className="h-24 w-32 rounded-md object-cover"
          />
          {(c.image_count as number) > 1 && (
            <span className="absolute bottom-1 end-1 rounded bg-black/60 px-1
                             text-[10px] text-white">
              {c.image_count as number}
            </span>
          )}
        </div>
      ) : (
        <div
          data-testid="no-image"
          className="grid h-24 w-32 shrink-0 place-items-center rounded-md
                     bg-chip text-xs text-muted"
        >
          No photo
        </div>
      )}

      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <Bidi
            as="h3"
            text={(c.location_display as string) || result.title}
            className="truncate font-semibold"
          />
          <SourceBadge sourceKey={result.source} />
        </div>

        <p className="mt-0.5 text-[15px] font-medium">
          {c.rent_display as string}
          {c.currency_inferred ? (
            <span title="Currency inferred, not stated in the listing"
                  className="ms-1 text-xs text-muted">
              *
            </span>
          ) : null}
        </p>

        {/* Rendered exactly as the server computed it. Never reconstructed
            from `bedrooms` -- one room of three is not a 3-bedroom unit
            (spec 8.2). */}
        <p data-testid="capacity" className="mt-0.5 text-sm text-muted">
          {c.capacity_display as string}
        </p>

        <div className="mt-1.5 flex flex-wrap gap-1">
          {((c.tenant_preference as string[]) ?? []).map((t) => (
            <Bidi
              key={t}
              as="span"
              text={t}
              className="rounded bg-chip px-1.5 py-0.5 text-[11px]"
            />
          ))}
        </div>

        <Disclosure label="Details">
          <dl className="space-y-1 text-sm text-muted">
            {c.bedrooms != null && (
              <div className="flex justify-between gap-4"><dt>Bedrooms</dt><dd>{c.bedrooms as number}</dd></div>
            )}
            {c.bathrooms != null && (
              <div className="flex justify-between gap-4"><dt>Bathrooms</dt><dd>{c.bathrooms as number}</dd></div>
            )}
            {c.furnishing && (
              <div className="flex justify-between gap-4"><dt>Furnishing</dt><dd>{c.furnishing as string}</dd></div>
            )}
            {c.floor && (
              <div className="flex justify-between gap-4"><dt>Floor</dt><dd>{c.floor as string}</dd></div>
            )}
            {c.square_feet != null && (
              <div className="flex justify-between gap-4"><dt>Area</dt><dd>{c.square_feet as number} sqft</dd></div>
            )}
            {c.has_lift != null && (
              <div className="flex justify-between gap-4"><dt>Lift</dt><dd>{c.has_lift ? "Yes" : "No"}</dd></div>
            )}
          </dl>
        </Disclosure>
      </div>
    </article>
  );
}
