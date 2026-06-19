import { useCallback, useEffect, useMemo, useState } from "react";
import { confirm, message, open } from "@tauri-apps/plugin-dialog";
import { api, type MetaEntry, type PreviewResult } from "../lib/ipc";
import { META_UPLOAD_FILTERS } from "../lib/metaUpload";
import { FilePreview } from "./FilePreview";
import { FileTree } from "./FileTree";
import { IndexButton } from "./IndexButton";
import { RenameMetaModal } from "./RenameMetaModal";
import { CreateMetaFolderModal } from "./CreateMetaFolderModal";

interface Props {
  projectId: string;
}

function joinPath(parent: string, name: string): string {
  if (!parent) return name;
  return `${parent}/${name}`;
}

function parentPath(path: string): string {
  if (!path.includes("/")) return "";
  return path.slice(0, path.lastIndexOf("/"));
}

export function MetaDocumentsTab({ projectId }: Props) {
  const [cwd, setCwd] = useState("");
  const [entries, setEntries] = useState<MetaEntry[]>([]);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [selectedKind, setSelectedKind] = useState<"file" | "dir" | null>(null);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [renameTarget, setRenameTarget] = useState<{
    path: string;
    kind: "file" | "dir";
  } | null>(null);
  const [createFolderOpen, setCreateFolderOpen] = useState(false);

  const breadcrumbs = useMemo(() => {
    if (!cwd) return [{ label: "meta", path: "" }];
    const parts = cwd.split("/");
    const crumbs = [{ label: "meta", path: "" }];
    let acc = "";
    for (const part of parts) {
      acc = joinPath(acc, part);
      crumbs.push({ label: part, path: acc });
    }
    return crumbs;
  }, [cwd]);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      setEntries(await api.listMetaDir(projectId, cwd));
    } catch (e) {
      setError(String(e));
      setEntries([]);
    }
  }, [projectId, cwd]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const selectFile = async (entry: MetaEntry) => {
    setSelectedPath(entry.path);
    setSelectedKind(entry.kind);
    if (entry.kind === "dir") {
      setPreview(null);
      return;
    }
    setLoadingPreview(true);
    try {
      setPreview(await api.previewFile(projectId, entry.path, "meta"));
    } finally {
      setLoadingPreview(false);
    }
  };

  const enterDir = (path: string) => {
    setCwd(path);
    setSelectedPath(null);
    setSelectedKind(null);
    setPreview(null);
  };

  const upload = async () => {
    const picked = await open({
      multiple: true,
      directory: false,
      filters: META_UPLOAD_FILTERS,
    });
    if (!picked) return;
    const paths = Array.isArray(picked) ? picked : [picked];
    setError(null);
    try {
      await api.uploadMetaFiles(projectId, paths, cwd || undefined);
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  const createFolder = () => {
    setCreateFolderOpen(true);
  };

  const rename = () => {
    if (!selectedPath || !selectedKind) return;
    setRenameTarget({ path: selectedPath, kind: selectedKind });
  };

  const moveToCurrentFolder = async () => {
    if (!selectedPath) return;
    const dest = cwd;
    if (parentPath(selectedPath) === dest) {
      await message("Already in this folder.", { title: "Move", kind: "info" });
      return;
    }
    const ok = await confirm(
      `Move "${selectedPath.split("/").pop()}" into ${dest || "meta root"}?`,
      { title: "Move here", kind: "warning" },
    );
    if (!ok) return;
    setError(null);
    try {
      const newPath = await api.moveMetaEntry(projectId, selectedPath, dest);
      setSelectedPath(newPath);
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  const remove = async () => {
    if (!selectedPath) return;
    const label = selectedKind === "dir" ? "folder and all contents" : "file";
    const ok = await confirm(`Delete ${label} "${selectedPath}"?`, {
      title: "Delete",
      kind: "warning",
    });
    if (!ok) return;
    setError(null);
    try {
      await api.deleteMetaEntry(projectId, selectedPath);
      setSelectedPath(null);
      setSelectedKind(null);
      setPreview(null);
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="mx-auto grid h-[calc(100vh-8rem)] max-w-5xl grid-cols-[300px_1fr] gap-4">
      <div className="flex flex-col overflow-hidden rounded-lg border border-cf-border bg-cf-surface">
        <div className="border-b border-cf-border px-3 py-2">
          <h3 className="text-sm font-semibold text-cf-ink">Meta documents</h3>
          <nav className="mt-1 flex flex-wrap items-center gap-1 text-xs text-cf-muted">
            {breadcrumbs.map((crumb, i) => (
              <span key={crumb.path || "root"} className="flex items-center gap-1">
                {i > 0 && <span>/</span>}
                <button
                  type="button"
                  className={
                    i === breadcrumbs.length - 1
                      ? "font-medium text-cf-ink"
                      : "text-cf-accent hover:underline"
                  }
                  onClick={() => enterDir(crumb.path)}
                >
                  {crumb.label}
                </button>
              </span>
            ))}
          </nav>
          <div className="mt-2 flex flex-wrap gap-1">
            <button
              type="button"
              className="rounded border border-cf-border px-2 py-0.5 text-xs text-cf-ink hover:bg-cf-surface-2"
              onClick={() => void upload()}
            >
              Upload files…
            </button>
            <button
              type="button"
              className="rounded border border-cf-border px-2 py-0.5 text-xs text-cf-ink hover:bg-cf-surface-2"
              onClick={() => void createFolder()}
            >
              New folder
            </button>
            {selectedPath && (
              <>
                <button
                  type="button"
                  className="rounded border border-cf-border px-2 py-0.5 text-xs text-cf-ink hover:bg-cf-surface-2"
                  onClick={rename}
                >
                  Rename
                </button>
                <button
                  type="button"
                  className="rounded border border-cf-border px-2 py-0.5 text-xs text-cf-ink hover:bg-cf-surface-2"
                  onClick={() => void moveToCurrentFolder()}
                >
                  Move here
                </button>
                <button
                  type="button"
                  className="rounded border border-cf-border px-2 py-0.5 text-xs text-cf-danger hover:bg-cf-surface-2"
                  onClick={() => void remove()}
                >
                  Delete
                </button>
              </>
            )}
          </div>
          {error && <p className="mt-2 text-xs text-cf-danger">{error}</p>}
        </div>
        <div className="flex-1 overflow-y-auto p-2">
          <FileTree
            entries={entries}
            selectedPath={selectedPath}
            onSelect={(e) => void selectFile(e)}
            onEnterDir={enterDir}
          />
        </div>
      </div>
      <div className="overflow-hidden rounded-lg border border-cf-border bg-cf-surface">
        {selectedPath && selectedKind === "file" && (
          <div className="flex items-center justify-end border-b border-cf-border px-3 py-1">
            <IndexButton projectId={projectId} itemId={`meta:${selectedPath}`} />
          </div>
        )}
        {selectedKind === "dir" && selectedPath ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-sm text-cf-muted">
            <p>Folder: {selectedPath}</p>
            <button
              type="button"
              className="rounded border border-cf-border px-3 py-1 text-xs text-cf-ink hover:bg-cf-surface-2"
              onClick={() => enterDir(selectedPath)}
            >
              Open folder
            </button>
          </div>
        ) : (
          <FilePreview preview={preview} loading={loadingPreview} />
        )}
      </div>
      <RenameMetaModal
        open={renameTarget !== null}
        projectId={projectId}
        path={renameTarget?.path ?? ""}
        kind={renameTarget?.kind ?? "file"}
        onClose={() => setRenameTarget(null)}
        onRenamed={(newPath) => {
          setSelectedPath(newPath);
          void refresh();
        }}
      />
      <CreateMetaFolderModal
        open={createFolderOpen}
        projectId={projectId}
        cwd={cwd}
        existingNames={entries.filter((e) => e.kind === "dir").map((e) => e.name)}
        onClose={() => setCreateFolderOpen(false)}
        onCreated={() => void refresh()}
      />
    </div>
  );
}
