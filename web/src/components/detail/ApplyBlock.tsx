"use client";

interface Method {
  kind: string;
  value: string;
  label_en?: string;
  label_dv?: string;
}

const MV = (n: string) => `+960${n.replace(/\D/g, "")}`;

function hrefFor(m: Method): string {
  switch (m.kind) {
    case "email": return `mailto:${m.value}`;
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

export function ApplyBlock({ methods }: { methods: Method[] }) {
  if (!methods.length) return null;
  return (
    <section className="space-y-2">
      <h2 className="text-sm font-semibold">How to apply</h2>
      <div className="flex flex-wrap gap-2">
        {methods.map((m, i) => (
          <a
            key={i}
            href={hrefFor(m)}
            target={m.kind === "form" || m.kind === "portal" ? "_blank" : undefined}
            rel="noopener noreferrer"
            className="rounded-full border border-line px-3 py-1.5 text-sm
                       hover:border-accent"
          >
            {LABEL[m.kind] ?? m.value}
          </a>
        ))}
      </div>
    </section>
  );
}
