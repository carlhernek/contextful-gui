import { useEffect, useRef, useState } from "react";
import { api } from "../lib/ipc";
import { validateMetaName } from "../lib/metaName";

interface Props {
  open: boolean;
  projectId: string;
  cwd: string;
  existingNames: string[];
  onClose: () => void;
  onCreated: (path: string) => void;
}

function joinPath(parent: string, name: string): string {
  if (!parent) return name;
  return `${parent}/${name}`;
}

export function CreateMetaFolderModal({
  open,
  projectId,
  cwd,
  existingNames,
  onClose,
  onCreated,
}: Props) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setName("");
    setError(null);
    setBusy(false);
    requestAnimationFrame(() => inputRef.current?.focus());
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const validationError = validateMetaName(name);
  const duplicate =
    name.trim() &&
    existingNames.some((n) => n.toLowerCase() === name.trim().toLowerCase())
      ? "A folder with that name already exists"
      : null;
  const fieldError = validationError ?? duplicate;
  const canSubmit = !busy && !fieldError && Boolean(name.trim());

  const submit = async () => {
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    const path = joinPath(cwd, name.trim());
    try {
      await api.createMetaDir(projectId, path);
      onCreated(path);
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!open) return null;

  const location = cwd ? `meta/${cwd}` : "meta";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-cf-bg/70">
      <div className="w-[420px] rounded-lg border border-cf-border bg-cf-surface p-5">
        <h3 className="mb-1 text-lg font-semibold text-cf-ink">New folder</h3>
        <p className="mb-4 text-xs text-cf-muted">Location: {location}</p>
        <label className="block text-xs font-medium text-cf-muted">
          Folder name
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
        {(fieldError || error) && (
          <p className="mt-2 text-xs text-cf-danger">{fieldError ?? error}</p>
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
            {busy ? "Creating…" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}
