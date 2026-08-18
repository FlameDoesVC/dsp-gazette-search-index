"use client";

import { createContext, useContext, type ReactNode } from "react";
import type { MetaOut, SourceOut } from "@/lib/api";

export const MetaContext = createContext<MetaOut | null>(null);

/**
 * The registry, fetched once on the server and handed down. Spec 10: a card
 * never issues its own request for an icon, and nothing about tabs, labels or
 * sources is hardcoded here.
 */
export function MetaProvider({
  meta,
  children,
}: {
  meta: MetaOut;
  children: ReactNode;
}) {
  return <MetaContext.Provider value={meta}>{children}</MetaContext.Provider>;
}

export function useMeta(): MetaOut | null {
  return useContext(MetaContext);
}

export function useSource(key: string): SourceOut | undefined {
  return useMeta()?.sources.find((s) => s.key === key);
}
