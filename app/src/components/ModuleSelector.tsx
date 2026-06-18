import { useEffect, useState } from "react";
import { api, type ModuleInfo } from "../lib/ipc";

const PACKS = ["Engineering", "Sales & Growth", "Onboarding & Docs", "Compliance & Risk"];

interface Props {
  projectId: string;
  selected: string[];
  onChange: (ids: string[]) => void;
}

export function ModuleSelector({ projectId, selected, onChange }: Props) {
  const [modules, setModules] = useState<ModuleInfo[]>([]);

  useEffect(() => {
    (async () => {
      const mods = await api.listModules(projectId);
      setModules(mods);
      try {
        const suggested = await api.getModuleSuggestions(projectId);
        if (suggested.length) onChange(suggested.filter((s) => mods.some((m) => m.id === s)));
      } catch {
        /* ignore */
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const toggle = (id: string) =>
    onChange(selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id]);

  const applyPack = (pack: string) => {
    const ids = modules.filter((m) => m.packs.includes(pack)).map((m) => m.id);
    onChange(Array.from(new Set([...selected, ...ids])));
  };

  return (
    <div className="rounded-lg border border-cf-border bg-cf-surface p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-semibold text-cf-ink">Modules</h3>
        <div className="flex flex-wrap gap-1">
          {PACKS.map((p) => (
            <button
              key={p}
              className="rounded-full border border-cf-border px-2 py-0.5 text-xs text-cf-muted hover:bg-cf-surface-2 hover:text-cf-ink"
              onClick={() => applyPack(p)}
            >
              {p}
            </button>
          ))}
          <button
            className="rounded-full border border-cf-border px-2 py-0.5 text-xs text-cf-muted hover:bg-cf-surface-2"
            onClick={() => onChange([])}
          >
            clear
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-1">
        {modules.map((m) => (
          <label
            key={m.id}
            className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-cf-surface-2"
          >
            <input
              type="checkbox"
              checked={selected.includes(m.id)}
              onChange={() => toggle(m.id)}
            />
            <span className="text-cf-ink">{m.title}</span>
          </label>
        ))}
      </div>
    </div>
  );
}
