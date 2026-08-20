"use client";

/**
 * Shared provenance affordances for entity-backed cards. Both read their
 * fields defensively: fixtures and non-entity results simply lack them.
 */

export function SimilarCount({ count }: { count?: number | null }) {
  if (!count || count === 1) return null;
  return <p className="text-xs text-base-content/60">{count} similar listings</p>;
}

export function ProfileNote({
  tier,
  inferredCount,
  fieldCount,
}: {
  tier?: string;
  inferredCount?: number | null;
  fieldCount?: number | null;
}) {
  if (!inferredCount || inferredCount <= 0) return null;
  return (
    <p className="text-xs text-base-content/60">
      {inferredCount} of {fieldCount} details from model knowledge
    </p>
  );
}
