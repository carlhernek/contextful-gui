import { useEffect, useRef, useState } from "react";

interface Props {
  value: string;
  models: string[];
  placeholder?: string;
  onChange: (value: string) => void;
}

export function ModelCombobox({ value, models, placeholder, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState(value);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => setQuery(value), [value]);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const filtered = models
    .filter((m) => m.toLowerCase().includes(query.toLowerCase()))
    .slice(0, 50);

  return (
    <div className="relative" ref={ref}>
      <input
        className="w-full rounded-md border border-cf-border bg-cf-surface-2 px-2 py-1.5 text-sm text-cf-ink outline-none focus:border-cf-accent"
        value={query}
        placeholder={placeholder ?? "model id"}
        onChange={(e) => {
          setQuery(e.target.value);
          onChange(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
      />
      {open && filtered.length > 0 && (
        <div className="absolute z-20 mt-1 max-h-56 w-full overflow-auto rounded-md border border-cf-border bg-cf-surface shadow-lg">
          {filtered.map((m) => (
            <button
              key={m}
              className="block w-full px-2 py-1.5 text-left text-sm text-cf-ink hover:bg-cf-surface-2"
              onClick={() => {
                onChange(m);
                setQuery(m);
                setOpen(false);
              }}
            >
              {m}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
