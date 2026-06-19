import { useEffect, useState } from "react";
import { api, type ModuleInfo } from "../lib/ipc";
import { PACKS, packFullySelected } from "../lib/modulePacks";
import { useJob } from "../lib/jobs";

interface Props {
  projectId: string;
  selected: string[];
  onChange: (ids: string[]) => void;
  refreshKey?: number;
}

const WORKSPACE_INDEX_ID = "workspace-index";

export function ModuleSelector({ projectId, selected, onChange, refreshKey = 0 }: Props) {
  const [modules, setModules] = useState<ModuleInfo[]>([]);
  const { isBusy } = useJob(undefined, projectId);

  useEffect(() => {
    void api.listModules(projectId).then(setModules);
  }, [projectId, refreshKey]);

  const hasWorkspaceIndex = modules.some((m) => m.id === WORKSPACE_INDEX_ID);
  const sorted = [...modules].sort((a, b) => {
    if (a.id === WORKSPACE_INDEX_ID) return -1;
    if (b.id === WORKSPACE_INDEX_ID) return 1;
    return a.title.localeCompare(b.title);
  });

  const toggle = (id: string) => {
    if (isBusy) return;
    onChange(selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id]);
  };

  const applyPack = (pack: string) => {
    if (isBusy) return;
    const ids = modules.filter((m) => m.packs.includes(pack)).map((m) => m.id);
    onChange(Array.from(new Set([...selected, ...ids])));
  };

  return (
    <div className="rounded-lg border border-cf-border bg-cf-surface p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-semibold text-cf-ink">Modules</h3>
        <div className="flex flex-wrap gap-1">
          {PACKS.map((p) => {
            const active = packFullySelected(p, modules, selected);
            return (
              <button
                key={p}
                type="button"
                className={`rounded-full border px-2 py-0.5 text-xs disabled:opacity-40 ${
                  active
                    ? "border-cf-accent bg-cf-accent text-cf-accent-ink"
                    : "border-cf-border text-cf-muted hover:bg-cf-surface-2 hover:text-cf-ink"
                }`}
                onClick={() => applyPack(p)}
                disabled={isBusy}
              >
                {p}
              </button>
            );
          })}
          <button
            type="button"
            className="rounded-full border border-cf-border px-2 py-0.5 text-xs text-cf-muted hover:bg-cf-surface-2 disabled:opacity-40"
            onClick={() => onChange([])}
            disabled={isBusy}
          >
            clear
          </button>
        </div>
      </div>

      {isBusy && (
        <p className="mb-2 text-xs text-cf-muted">Module selection locked while a job is running.</p>
      )}

      {!hasWorkspaceIndex && (
        <p className="mb-3 rounded-md border border-cf-warning/40 bg-cf-warning/10 px-3 py-2 text-xs text-cf-warning">
          <strong>Workspace Index</strong> is not installed in this project (old module pack).
          Use <strong>Update modules</strong> in the header (next to “modules v…”) to pull the latest
          pack — it includes indexing.
        </p>
      )}

      <div className="grid grid-cols-2 gap-1">
        {sorted.map((m) => (
          <label
            key={m.id}
            className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-sm ${
              isBusy ? "cursor-not-allowed opacity-60" : "cursor-pointer hover:bg-cf-surface-2"
            }`}
          >
            <input
              type="checkbox"
              checked={selected.includes(m.id)}
              onChange={() => toggle(m.id)}
              disabled={isBusy}
            />
            <span className="text-cf-ink">
              {m.title}
              {m.id === WORKSPACE_INDEX_ID && (
                <span className="ml-1 text-xs text-cf-muted">(indexing)</span>
              )}
            </span>
          </label>
        ))}
      </div>
    </div>
  );
}
