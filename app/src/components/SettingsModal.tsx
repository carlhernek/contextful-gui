import { useEffect, useState } from "react";
import { api, type ModuleInfo } from "../lib/ipc";
import { ModelCombobox } from "./ModelCombobox";
import { Spinner } from "./Spinner";

interface Props {
  projectId: string;
  projectType: string;
  onClose: () => void;
  onSaved: () => void;
}

export function SettingsModal({ projectId, projectType, onClose, onSaved }: Props) {
  const [modelIds, setModelIds] = useState<string[]>([]);
  const [models, setModels] = useState<Record<string, string>>({
    orchestrator: "deepseek/deepseek-v4-flash",
    module: "deepseek/deepseek-v4-flash",
  });
  const [modules, setModules] = useState<ModuleInfo[]>([]);
  const [type, setType] = useState(projectType);
  const [loadingModels, setLoadingModels] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      setModules(await api.listModules(projectId));
      setLoadingModels(true);
      try {
        await api.configureSidecar(projectId);
        const res = await api.listModels();
        setModelIds(
          (res.models as { id?: string }[]).map((m) => m.id ?? "").filter(Boolean)
        );
      } catch (e) {
        setError(`Could not load model list: ${e}`);
      } finally {
        setLoadingModels(false);
      }
    })();
  }, [projectId]);

  const setModel = (key: string, value: string) =>
    setModels((m) => ({ ...m, [key]: value }));

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const cleaned = Object.fromEntries(Object.entries(models).filter(([, v]) => v.trim()));
      await api.setModels(projectId, cleaned);
      await api.setProjectType(projectId, type);
      onSaved();
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-cf-bg/70">
      <div className="max-h-[85vh] w-[560px] overflow-auto rounded-lg border border-cf-border bg-cf-surface p-5">
        <h2 className="mb-4 text-lg font-semibold text-cf-ink">Project settings</h2>

        <label className="mb-1 block text-sm text-cf-muted">Project type</label>
        <select
          className="mb-4 w-full rounded-md border border-cf-border bg-cf-surface-2 px-2 py-1.5 text-sm text-cf-ink"
          value={type}
          onChange={(e) => setType(e.target.value)}
        >
          <option value="both">both</option>
          <option value="b2c">b2c</option>
          <option value="b2b">b2b</option>
        </select>

        <div className="mb-2 flex items-center gap-2">
          <h3 className="text-sm font-semibold text-cf-ink">Models</h3>
          {loadingModels && <Spinner size={12} />}
        </div>

        <div className="space-y-3">
          {(["orchestrator", "module"] as const).map((role) => (
            <div key={role}>
              <label className="mb-1 block text-xs text-cf-muted">{role}</label>
              <ModelCombobox
                value={models[role] ?? ""}
                models={modelIds}
                onChange={(v) => setModel(role, v)}
              />
            </div>
          ))}

          <details>
            <summary className="cursor-pointer text-xs text-cf-muted">
              per-module overrides
            </summary>
            <div className="mt-2 space-y-2">
              {modules.map((m) => (
                <div key={m.id}>
                  <label className="mb-1 block text-xs text-cf-muted">{m.title}</label>
                  <ModelCombobox
                    value={models[m.id] ?? ""}
                    models={modelIds}
                    placeholder="(inherits module default)"
                    onChange={(v) => setModel(m.id, v)}
                  />
                </div>
              ))}
            </div>
          </details>
        </div>

        {error && <p className="mt-3 text-sm text-cf-danger">{error}</p>}

        <div className="mt-5 flex justify-end gap-2">
          <button
            className="rounded-md border border-cf-border px-3 py-1.5 text-sm text-cf-ink hover:bg-cf-surface-2"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            className="rounded-md bg-cf-accent px-3 py-1.5 text-sm font-medium text-cf-accent-ink hover:opacity-90"
            onClick={save}
            disabled={busy}
          >
            {busy ? <Spinner size={12} /> : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
