"use client";

import { useEffect, useRef, useState } from "react";
import { getSuggest } from "@/lib/api";
import { dirFor, langFor } from "@/lib/script";

const DEBOUNCE_MS = 200;
const MIN_SUGGEST_LEN = 2;

export function SearchBox({
  initial, onSubmit,
}: {
  initial: string;
  onSubmit: (q: string) => void;
}) {
  const [value, setValue] = useState(initial);
  const [suggestions, setSuggestions] = useState<{ term: string }[]>([]);
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    if (value.trim().length < MIN_SUGGEST_LEN) {
      setSuggestions([]);
      return;
    }
    timer.current = setTimeout(() => {
      getSuggest(value)
        .then((r) => {
          setSuggestions(r.suggestions);
          setOpen(true);
        })
        .catch(() => setSuggestions([]));
    }, DEBOUNCE_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [value]);

  return (
    <form
      role="search"
      className="relative"
      onSubmit={(e) => {
        e.preventDefault();
        setOpen(false);
        onSubmit(value.trim());
      }}
    >
      <input
        type="search"
        value={value}
        // The field itself flips as the user types, so a Thaana query does not
        // type backwards into an LTR box. Spec 10, per-element.
        dir={dirFor(value)}
        lang={langFor(value)}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        placeholder="Search"
        className="w-full rounded-full border border-line px-4 py-2.5 text-base
                   outline-none focus:border-accent"
      />
      {open && suggestions.length > 0 && (
        <ul className="absolute z-10 mt-1 w-full overflow-hidden rounded-lg
                       border border-line bg-bg shadow-lg">
          {suggestions.map((s) => (
            <li key={s.term}>
              <button
                type="button"
                dir={dirFor(s.term)}
                lang={langFor(s.term)}
                className="block w-full px-4 py-2 text-start text-sm hover:bg-chip"
                onMouseDown={() => {
                  setValue(s.term);
                  setOpen(false);
                  onSubmit(s.term);
                }}
              >
                {s.term}
              </button>
            </li>
          ))}
        </ul>
      )}
    </form>
  );
}
