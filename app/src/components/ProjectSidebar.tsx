import { useState } from "react";
import { api, type Project } from "../lib/ipc";
import { ConfirmModal } from "./ConfirmModal";

interface Props {
  projects: Project[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onChanged: () => void;
}

export function ProjectSidebar({ projects, activeId, onSelect, onChanged }: Props) {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [pendingDelete, setPendingDelete] = useState<Project | null>(null);

  const create = async () => {
    const display = name.trim();
    if (!display) return;
    const id = await api.suggestProjectId(display);
    await api.createProject(id, display);
    await api.setActiveProject(id);
    setName("");
    setCreating(false);
    onChanged();
    onSelect(id);
  };

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    await api.deleteProject(pendingDelete.id);
    setPendingDelete(null);
    onChanged();
  };

  return (
    <aside className="flex w-64 flex-col border-r border-cf-border bg-cf-surface">
      <div className="flex items-center justify-between px-4 py-3">
        <span className="text-sm font-semibold text-cf-ink">Projects</span>
        <button
          className="rounded-md bg-cf-accent px-2 py-0.5 text-sm font-medium text-cf-accent-ink hover:opacity-90"
          onClick={() => setCreating((v) => !v)}
        >
          +
        </button>
      </div>

      {creating && (
        <div className="flex gap-1 px-3 pb-2">
          <input
            autoFocus
            className="flex-1 rounded-md border border-cf-border bg-cf-surface-2 px-2 py-1 text-sm text-cf-ink outline-none focus:border-cf-accent"
            placeholder="Project name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && create()}
          />
          <button
            className="rounded-md border border-cf-border px-2 text-sm text-cf-ink hover:bg-cf-surface-2"
            onClick={create}
          >
            ✓
          </button>
        </div>
      )}

      <div className="flex-1 overflow-auto">
        {projects.length === 0 && (
          <p className="px-4 py-6 text-center text-xs text-cf-muted">
            No projects yet. Create one to begin.
          </p>
        )}
        {projects.map((p) => (
          <div
            key={p.id}
            className={`group flex items-center justify-between px-4 py-2 text-sm ${
              p.id === activeId
                ? "bg-cf-surface-2 text-cf-ink"
                : "text-cf-muted hover:bg-cf-surface-2/50"
            }`}
          >
            <button className="flex-1 text-left" onClick={() => onSelect(p.id)}>
              <span className="block truncate">{p.display_name}</span>
              <span className="text-xs text-cf-muted">{p.project_type}</span>
            </button>
            <button
              className="opacity-0 group-hover:opacity-100 text-cf-danger"
              onClick={() => setPendingDelete(p)}
              title="Delete project"
            >
              ✕
            </button>
          </div>
        ))}
      </div>

      <ConfirmModal
        open={!!pendingDelete}
        title="Delete project"
        body={`Hide "${pendingDelete?.display_name}" from the list? The folder is retained on disk.`}
        confirmLabel="Delete"
        danger
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </aside>
  );
}
