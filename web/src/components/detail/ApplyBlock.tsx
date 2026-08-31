"use client";

import { Bidi } from "@/components/Bidi";
import { UI_DV } from "@/lib/uiLabels";

interface Method {
  kind: string;
  value: string;
  label_en?: string;
  label_dv?: string;
}

const MV = (n: string) => `+960${n.replace(/\D/g, "")}`;

// Enrichment sometimes tags a bare application-portal URL as "email" when the
// notice actually says "apply via our website" -- only a value that is
// actually shaped like an address gets a mailto: link.
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function hrefFor(m: Method): string {
  switch (m.kind) {
    case "email": return EMAIL_RE.test(m.value) ? `mailto:${m.value}` : m.value;
    case "phone": return `tel:${MV(m.value)}`;
    case "viber": return `viber://chat?number=${encodeURIComponent(MV(m.value))}`;
    case "whatsapp": return `https://wa.me/${MV(m.value).replace("+", "")}`;
    default: return m.value;
  }
}

const LABEL: Record<string, string> = {
  form: "Apply via form", portal: "Apply on the portal",
  walk_in: "Apply in person", post: "Apply by post",
};

export function ApplyBlock({
  methods, headingLevel: Heading = "h2", dv = false,
}: {
  methods: Method[];
  /** See CompensationTable's headingLevel: h2 on the detail page (under its
   * h1), h3 inside a result card (whose own title is already an h3). */
  headingLevel?: "h2" | "h3";
  /** Whether the surrounding document is Dhivehi-dominant -- picks
   * `label_dv` over `label_en` for the model's own label when both exist. */
  dv?: boolean;
}) {
  if (!methods.length) return null;
  const hasForm = methods.some((m) => m.kind === "form");
  return (
    <section className="space-y-2">
      <Bidi as={Heading} className="text-sm font-semibold"
            text={dv ? UI_DV.howToApply : "How to apply"} />
      <div className="flex flex-wrap gap-2">
        {methods.map((m, i) => {
          const href = hrefFor(m);
          const isForm = m.kind === "form";
          const primary = isForm || (!hasForm && i === 0);
          return (
            <a
              key={i}
              href={href}
              target={href.startsWith("http") ? "_blank" : undefined}
              rel="noopener noreferrer"
              className={`btn btn-sm ${primary ? "btn-outline btn-primary" : ""} ${isForm ? "btn-block" : ""}`}
            >
              {/* The model's own label wins when it gave one (real gazette
                  examples: "Company website", "Online application form") --
                  it is more specific than a kind-based generic. */}
              <Bidi as="span" text={
                (dv && m.label_dv) || m.label_en ||
                (dv ? UI_DV.applyLabel[m.kind] : LABEL[m.kind]) || m.value
              } />
            </a>
          );
        })}
      </div>
    </section>
  );
}
