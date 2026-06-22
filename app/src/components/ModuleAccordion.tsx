import type { ModuleInfo } from "../lib/ipc";
import {
  PACKS,
  type PackName,
  groupModulesByPrimaryPack,
  packModuleIds,
  secondaryPacks,
  sectionSelectionCount,
} from "../lib/modulePacks";

const WORKSPACE_INDEX_ID = "workspace-index";

interface Props {
  modules: ModuleInfo[];
  selected: string[];
  disabled?: boolean;
  onToggle: (id: string) => void;
  onSelectPack: (ids: string[]) => void;
  onClearPack: (ids: string[]) => void;
}

export function ModuleAccordion({
  modules,
  selected,
  disabled = false,
  onToggle,
  onSelectPack,
  onClearPack,
}: Props) {
  const groups = groupModulesByPrimaryPack(modules);

  return (
    <div className="space-y-2">
      {PACKS.map((pack) => {
        const packModules = groups.get(pack as PackName) ?? [];
        if (packModules.length === 0) return null;
        const { selected: selCount, total } = sectionSelectionCount(packModules, selected);
        const packIds = packModuleIds(modules, pack);
        const defaultOpen = pack === "Core";

        return (
          <details
            key={pack}
            open={defaultOpen}
            className="rounded-md border border-cf-border bg-cf-surface-2/40"
          >
            <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm font-medium text-cf-ink [&::-webkit-details-marker]:hidden">
              <span className="text-cf-muted">▸</span>
              <span className="flex-1">{pack}</span>
              <span className="text-xs font-normal text-cf-muted">
                {selCount}/{total} selected
              </span>
              {!disabled && (
                <span className="flex gap-2 text-xs font-normal" onClick={(e) => e.preventDefault()}>
                  <button
                    type="button"
                    className="text-cf-accent hover:underline"
                    onClick={() => onSelectPack(packIds)}
                  >
                    all
                  </button>
                  <button
                    type="button"
                    className="text-cf-muted hover:text-cf-ink hover:underline"
                    onClick={() => onClearPack(packIds)}
                  >
                    clear
                  </button>
                </span>
              )}
            </summary>
            <ul className="border-t border-cf-border px-2 py-1">
              {packModules.map((m) => {
                const extra = secondaryPacks(m);
                return (
                  <li key={m.id}>
                    <label
                      className={`flex gap-2 rounded-md px-2 py-2 ${
                        disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer hover:bg-cf-surface-2"
                      }`}
                    >
                      <input
                        type="checkbox"
                        className="mt-0.5 shrink-0"
                        checked={selected.includes(m.id)}
                        onChange={() => onToggle(m.id)}
                        disabled={disabled}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="text-sm text-cf-ink">
                          {m.title}
                          {m.id === WORKSPACE_INDEX_ID && (
                            <span className="ml-1 text-xs text-cf-muted">(indexing)</span>
                          )}
                        </span>
                        {m.description && (
                          <span className="mt-0.5 block text-xs leading-snug text-cf-muted">
                            {m.description}
                          </span>
                        )}
                        {extra.length > 0 && (
                          <span className="mt-0.5 block text-xs text-cf-muted/80">
                            also {extra.join(", ")}
                          </span>
                        )}
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          </details>
        );
      })}
    </div>
  );
}
