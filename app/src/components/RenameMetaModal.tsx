import { useEffect, useRef, useState } from "react";
import { api } from "../lib/ipc";
import { validateMetaName } from "../lib/metaName";

interface Props {
  open: boolean;
  projectId: string;
  path: string;
  kind: "file" | "dir";
  onClose: () => void;
  onRenamed: (newPath: string) => void;
}

function parentFolder(path: string): string {
  if (!path.includes("/")) return "meta";
  return `meta/${path.slice(0, path.lastIndexOf("/"))}`;
}

export function RenameMetaModal({ open, projectId, path, kind, onClose, onRenamed }: Props) {
  const currentName = path.split("/").pop() ?? path;
  const [name, setName] = useState(currentName);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setName(currentName);
    setError(null);
    setBusy(false);
    requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    });
  }, [open, currentName]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const validationError = validateMetaName(name);
  const unchanged = name.trim() === currentName;
  const canSubmit = !busy && !validationError && !unchanged;

  const submit = async () => {
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      const newPath = await api.renameMetaEntry(projectId, path, name.trim());
      onRenamed(newPath);
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-cf-bg/70">
      <div className="w-[420px] rounded-lg border border-cf-border bg-cf-surface p-5">
        <h3 className="mb-1 text-lg font-semibold text-cf-ink">Rename {kind}</h3>
        <p className="mb-4 font-mono text-xs text-cf-muted">
          {parentFolder(path)}/{currentName}
        </p>
        <label className="block text-xs font-medium text-cf-muted">
          New name
          <input
            ref={inputRef}
            className="mt-1 w-full rounded-md border border-cf-border bg-cf-surface-2 px-2 py-1.5 text-sm text-cf-ink outline-none focus:border-cf-accent"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && canSubmit) void submit();
            }}
          />
        </label>
        {(validationError || error) && (
          <p className="mt-2 text-xs text-cf-danger">{validationError ?? error}</p>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            className="rounded-md border border-cf-border px-3 py-1.5 text-sm text-cf-ink hover:bg-cf-surface-2"
            onClick={onClose}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            type="button"
            className="rounded-md bg-cf-accent px-3 py-1.5 text-sm font-medium text-cf-accent-ink hover:opacity-90 disabled:opacity-50"
            onClick={() => void submit()}
            disabled={!canSubmit}
          >
            {busy ? "Renaming…" : "Rename"}
          </button>
        </div>
      </div>
    </div>
  );
}
