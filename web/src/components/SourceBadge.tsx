"use client";

import { Bidi } from "./Bidi";
import { useSource } from "./MetaProvider";

const SIZES = { sm: 16, md: 20 } as const;

/**
 * Source attribution. Spec 8.5.
 *
 * Three rules, all of them load-bearing:
 *  - always paired with a label, never a bare icon
 *  - 16px in dense contexts, 20px on cards
 *  - placed with `inline-start`, never `left`, so it flips with dir
 */
export function SourceBadge({
  sourceKey,
  size = "md",
  lang = "en",
}: {
  sourceKey: string;
  size?: keyof typeof SIZES;
  lang?: "en" | "dv";
}) {
  const source = useSource(sourceKey);
  if (!source) return null;

  const px = SIZES[size];
  const label = lang === "dv" && source.label_dv ? source.label_dv : source.label_en;

  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted ms-0">
      {source.icon ? (
        <img
          src={source.icon}
          alt=""
          width={px}
          height={px}
          className="shrink-0 rounded-[2px]"
          loading="lazy"
        />
      ) : (
        <span
          aria-hidden="true"
          style={{ width: px, height: px }}
          className="grid shrink-0 place-items-center rounded-[3px] bg-chip
                     text-[9px] font-semibold"
        >
          {source.icon_fallback_text || source.label_en.slice(0, 2)}
        </span>
      )}
      <Bidi as="span" text={label} />
    </span>
  );
}
