import type { MetaEntry } from "../lib/ipc";

interface Props {
  entries: MetaEntry[];
  selectedPath: string | null;
  onSelect: (entry: MetaEntry) => void;
  onEnterDir: (path: string) => void;
}

export function FileTree({ entries, selectedPath, onSelect, onEnterDir }: Props) {
  if (entries.length === 0) {
    return <p className="px-2 py-1 text-xs text-cf-muted">Empty folder — upload files or create a folder.</p>;
  }

  return (
    <ul className="space-y-0.5">
      {entries.map((e) => (
        <li key={e.path}>
          <div
            className={`flex w-full items-center gap-1 rounded px-1 ${
              selectedPath === e.path ? "bg-cf-surface-2" : "hover:bg-cf-surface-2"
            }`}
          >
            <button
              type="button"
              className={`min-w-0 flex-1 truncate px-1 py-1 text-left text-sm ${
                selectedPath === e.path ? "text-cf-ink" : "text-cf-muted hover:text-cf-ink"
              }`}
              onClick={() => onSelect(e)}
            >
              <span className="mr-1">{e.kind === "dir" ? "📁" : "📄"}</span>
              {e.name}
            </button>
            {e.kind === "dir" && (
              <button
                type="button"
                title="Open folder"
                className="shrink-0 rounded px-1.5 py-0.5 text-xs text-cf-accent hover:bg-cf-surface"
                onClick={() => onEnterDir(e.path)}
              >
                →
              </button>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
