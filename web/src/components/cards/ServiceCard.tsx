"use client";

import { Bidi } from "@/components/Bidi";
import type { ResultOut } from "@/lib/api";
import { CardShell } from "./CardShell";
import { ProfileNote, SimilarCount } from "./EntityMeta";

const RATE_BASIS_LABEL: Record<string, string> = {
  per_job: "Per job",
  per_hour: "Per hour",
  per_visit: "Per visit",
  quote_only: "Quote only",
};

/**
 * An entity-backed service listing. A service has no price, condition, brand
 * or spec chips; what it has is what the tradesman offers, where they cover
 * and how to reach them.
 */
export function ServiceCard({ result }: { result: ResultOut }) {
  const c = result.card as Record<string, never>;
  const services = (c.services_offered as string[]) ?? [];
  const coverage = (c.coverage as string[]) ?? [];
  const phone = c.phone as string | undefined;
  const rateBasis = c.rate_basis as string | undefined;

  return (
    <CardShell
      result={result}
      title={(c.title as string) || result.title}
      summary={(c.summary as string) || result.summary || undefined}
    >
      {services.length ? (
        <div className="mt-1 flex flex-wrap gap-1">
          {services.slice(0, 6).map((s) => (
            <Bidi key={s} as="span" text={s} className="badge badge-sm" />
          ))}
        </div>
      ) : null}

      {coverage.length ? (
        <p className="mt-1 text-xs text-base-content/60">
          {coverage.slice(0, 5).map((v, i) => (
            <span key={v}>
              {/* A comma, not a middle dot: these are place names, and a
                  comma is both the natural separator for a list of them and
                  ASCII, which the rest of this codebase holds to. */}
              {i > 0 ? ", " : ""}
              <Bidi as="span" text={v} />
            </span>
          ))}
        </p>
      ) : null}

      {phone ? (
        <p className="mt-1">
          <a href={`tel:${phone}`} className="btn btn-sm btn-primary">
            <Bidi as="span" text={phone} />
          </a>
        </p>
      ) : null}

      {rateBasis ? (
        <p className="mt-1 text-xs text-base-content/60">
          {RATE_BASIS_LABEL[rateBasis] ?? rateBasis}
        </p>
      ) : null}

      <SimilarCount count={c.listing_count as number | undefined} />
      <ProfileNote
        tier={c.profile_tier as string | undefined}
        inferredCount={c.inferred_count as number | undefined}
        fieldCount={c.field_count as number | undefined}
      />
    </CardShell>
  );
}
