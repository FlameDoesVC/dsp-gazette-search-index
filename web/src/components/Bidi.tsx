import { createElement, type ElementType } from "react";
import { dirFor, langFor } from "@/lib/script";

type Props = {
  text: string | null | undefined;
  as?: ElementType;
  className?: string;
} & Record<string, unknown>;

/**
 * Per-element direction. Spec 10.
 *
 * Every piece of user-facing text from the corpus goes through this. The
 * alternative -- flipping the page or a container -- is wrong the moment one
 * result is Thaana and its neighbour is Latin, which in this corpus is the
 * normal case rather than the edge case.
 */
export function Bidi({ text, as = "span", className, ...rest }: Props) {
  if (!text) return null;
  return createElement(
    as,
    { dir: dirFor(text), lang: langFor(text), className, ...rest },
    text
  );
}
