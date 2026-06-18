import type { MetaEntry } from "../lib/ipc";

interface Props {
  entries: MetaEntry[];
  selectedPath: string | null;
  expandedDirs: Set<string>;
  onSelect: (entry: MetaEntry) => void;
  onToggleDir: (path: string) => void;
}

export function FileTree({ entries, selectedPath, expandedDirs, onSelect, onToggleDir }: Props) {
  if (entries.length === 0) {
    return <p className="px-2 py-1 text-xs text-cf-muted">Empty folder</p>;
  }

  return (
    <ul className="space-y-0.5">
      {entries.map((e) => (
        <li key={e.path}>
          <button
            type="button"
            className={`flex w-full items-center gap-1 rounded px-2 py-1 text-left text-sm ${
              selectedPath === e.path
                ? "bg-cf-surface-2 text-cf-ink"
                : "text-cf-muted hover:bg-cf-surface-2 hover:text-cf-ink"
            }`}
            onClick={() => {
              if (e.kind === "dir") {
                onToggleDir(e.path);
              } else {
                onSelect(e);
              }
            }}
          >
            <span>{e.kind === "dir" ? (expandedDirs.has(e.path) ? "▾" : "▸") : "📄"}</span>
            <span className="truncate">{e.name}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
