"use client";

import type { ResultOut } from "@/lib/api";
import { JobCard } from "./JobCard";
import { NewsCard } from "./NewsCard";
import { PropertyCard } from "./PropertyCard";
import { ServiceCard } from "./ServiceCard";
import { ShoppingCard } from "./ShoppingCard";

const CARDS = {
  job: JobCard,
  property: PropertyCard,
  shopping: ShoppingCard,
  news: NewsCard,
} as const;

export function ResultCard({ result }: { result: ResultOut }) {
  const card = result.card as Record<string, never>;
  // Entity-backed results route on `kind` first: a service listing stays
  // doc_type "shopping" by design, so the kind is what picks ServiceCard.
  if (card.kind === "service") return <ServiceCard result={result} />;
  // news is the default sink (spec 5.3), so an unknown doc_type rendering as
  // news is correct rather than a fallback.
  const Card = CARDS[result.doc_type as keyof typeof CARDS] ?? NewsCard;
  return <Card result={result} />;
}
