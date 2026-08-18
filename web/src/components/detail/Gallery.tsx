"use client";

import { useState } from "react";

/**
 * Thumbnail strip with a selected main image. Plain <img> with loading="lazy":
 * product images are third-party URLs on hosts we do not control, so the Next
 * optimizer is off and the strips stay unoptimized (spec 12.5).
 */
export function Gallery({ images }: { images: string[] }) {
  const [selected, setSelected] = useState(0);
  if (!images.length) return null;
  const main = images[selected] ?? images[0];

  return (
    <div className="space-y-2">
      <img
        src={main}
        alt=""
        loading="lazy"
        className="aspect-square w-full rounded-lg object-cover"
      />
      {images.length > 1 && (
        <div className="flex gap-2 overflow-x-auto">
          {images.map((src, i) => (
            <button
              key={src}
              type="button"
              onClick={() => setSelected(i)}
              aria-label={`Photo ${i + 1} of ${images.length}`}
              className={
                "shrink-0 overflow-hidden rounded-md border-2 " +
                (i === selected ? "border-accent" : "border-transparent")
              }
            >
              <img src={src} alt="" loading="lazy" className="h-16 w-16 object-cover" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
