import { useCallback, useEffect, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { api, type MetaEntry, type PreviewResult } from "../lib/ipc";
import { FilePreview } from "./FilePreview";
import { FileTree } from "./FileTree";
import { IndexButton } from "./IndexButton";

interface Props {
  projectId: string;
}

export function MetaDocumentsTab({ projectId }: Props) {
  const [cwd, setCwd] = useState("");
  const [entries, setEntries] = useState<MetaEntry[]>([]);
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(false);

  const refresh = useCallback(async () => {
    setEntries(await api.listMetaDir(projectId, cwd));
  }, [projectId, cwd]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const selectFile = async (entry: MetaEntry) => {
    setSelectedPath(entry.path);
    setLoadingPreview(true);
    try {
      setPreview(await api.previewFile(projectId, entry.path, "meta"));
    } finally {
      setLoadingPreview(false);
    }
  };

  const toggleDir = (path: string) => {
    setExpandedDirs((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
    setCwd(path);
  };

  const upload = async () => {
    const picked = await open({ multiple: true, directory: true });
    if (!picked) return;
    const paths = Array.isArray(picked) ? picked : [picked];
    await api.uploadMetaFiles(projectId, paths);
    await refresh();
  };

  const remove = async () => {
    if (!selectedPath) return;
    await api.deleteMetaEntry(projectId, selectedPath);
    setSelectedPath(null);
    setPreview(null);
    await refresh();
  };

  return (
    <div className="mx-auto grid h-[calc(100vh-8rem)] max-w-5xl grid-cols-[280px_1fr] gap-4">
      <div className="flex flex-col overflow-hidden rounded-lg border border-cf-border bg-cf-surface">
        <div className="flex items-center justify-between border-b border-cf-border px-3 py-2">
          <div>
            <h3 className="text-sm font-semibold text-cf-ink">Meta documents</h3>
            {cwd && (
              <button
                type="button"
                className="text-xs text-cf-accent hover:underline"
                onClick={() => {
                  const parent = cwd.includes("/") ? cwd.slice(0, cwd.lastIndexOf("/")) : "";
                  setCwd(parent);
                }}
              >
                ↑ {cwd || "root"}
              </button>
            )}
          </div>
          <div className="flex gap-1">
            <button
              type="button"
              className="rounded border border-cf-border px-2 py-0.5 text-xs text-cf-ink hover:bg-cf-surface-2"
              onClick={() => void upload()}
            >
              Upload…
            </button>
            {selectedPath && (
              <button
                type="button"
                className="rounded border border-cf-border px-2 py-0.5 text-xs text-cf-danger hover:bg-cf-surface-2"
                onClick={() => void remove()}
              >
                Delete
              </button>
            )}
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-2">
          <FileTree
            entries={entries}
            selectedPath={selectedPath}
            expandedDirs={expandedDirs}
            onSelect={(e) => void selectFile(e)}
            onToggleDir={toggleDir}
          />
        </div>
      </div>
      <div className="overflow-hidden rounded-lg border border-cf-border bg-cf-surface">
        {selectedPath && (
          <div className="flex items-center justify-end border-b border-cf-border px-3 py-1">
            <IndexButton projectId={projectId} itemId={`meta:${selectedPath}`} />
          </div>
        )}
        <FilePreview preview={preview} loading={loadingPreview} />
      </div>
    </div>
  );
}
