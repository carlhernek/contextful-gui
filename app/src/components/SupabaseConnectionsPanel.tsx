import { useEffect, useState } from "react";
import { api, type SupabaseConnection, type SupabaseProject } from "../lib/ipc";
import { useJob } from "../lib/jobs";
import { IndexButton } from "./IndexButton";
import { Spinner } from "./Spinner";

const TOKENS_URL = "https://supabase.com/dashboard/account/tokens";

export function SupabaseConnectionsPanel({ projectId }: { projectId: string }) {
  const [masked, setMasked] = useState<string | null>(null);
  const [token, setToken] = useState("");
  const [tokenBusy, setTokenBusy] = useState(false);
  const [projects, setProjects] = useState<SupabaseProject[] | null>(null);
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [loadingProjects, setLoadingProjects] = useState(false);
  const [connections, setConnections] = useState<SupabaseConnection[]>([]);
  const [snapshotTarget, setSnapshotTarget] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { busy: snapshotBusy } = useJob("snapshot", projectId);
  const { isBusy } = useJob(undefined, projectId);
  const busy = snapshotBusy || isBusy;

  const refresh = async () => {
    setMasked(await api.storedSupabaseTokenMasked());
    setConnections(await api.listSupabase(projectId));
  };

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const saveToken = async () => {
    const t = token.trim();
    if (!t) return;
    setTokenBusy(true);
    setError(null);
    try {
      await api.setSupabaseToken(t);
      setToken("");
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setTokenBusy(false);
    }
  };

  const clearToken = async () => {
    setTokenBusy(true);
    setError(null);
    try {
      await api.clearSupabaseToken();
      setProjects(null);
      setChecked({});
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setTokenBusy(false);
    }
  };

  const loadProjects = async () => {
    setLoadingProjects(true);
    setError(null);
    try {
      const res = await api.listSupabaseProjects();
      setProjects(res.projects);
      setChecked({});
    } catch (e) {
      setError(String(e));
    } finally {
      setLoadingProjects(false);
    }
  };

  const existingRefs = new Set(connections.map((c) => c.project_ref));

  const addSelected = async () => {
    if (!projects) return;
    setError(null);
    const toAdd = projects.filter((p) => checked[p.ref] && !existingRefs.has(p.ref));
    try {
      for (const p of toAdd) {
        await api.addSupabase(projectId, p.name, p.ref, p.region ?? null);
      }
      setChecked({});
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  const runSnapshot = async (projectRef: string) => {
    setSnapshotTarget(projectRef);
    setError(null);
    try {
      await api.snapshotSupabase(projectId, projectRef);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setSnapshotTarget(null);
    }
  };

  const remove = async (projectRef: string) => {
    setError(null);
    try {
      await api.removeSupabase(projectId, projectRef);
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  const selectedCount = projects
    ? projects.filter((p) => checked[p.ref] && !existingRefs.has(p.ref)).length
    : 0;

  return (
    <div className="mt-6">
      <h3 className="mb-2 font-semibold text-cf-ink">Supabase projects</h3>
      <div className="mb-3 rounded-md border border-cf-border bg-cf-surface-2 px-3 py-2 text-xs text-cf-muted">
        Connect via the Supabase{" "}
        <a className="text-cf-info hover:underline" href={TOKENS_URL} target="_blank" rel="noreferrer">
          Management API personal access token
        </a>
        . Contextful only issues read-only configuration and advisor calls — no SQL and no database
        contents are ever read. Note: a PAT is account-level and full-privilege; it is stored in the
        OS keychain and sent only to api.supabase.com.
      </div>

      <div className="mb-4 rounded-md border border-cf-border bg-cf-surface-2 p-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <input
            type="password"
            className="min-w-[16rem] flex-1 rounded-md border border-cf-border bg-cf-surface px-2 py-1.5 text-sm text-cf-ink"
            placeholder={masked ? `Saved: ${masked}` : "sbp_… Management access token"}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            disabled={tokenBusy || busy}
          />
          <button
            type="button"
            className="rounded-md bg-cf-accent px-3 py-1.5 text-sm font-medium text-cf-accent-ink hover:opacity-90 disabled:opacity-40"
            onClick={() => void saveToken()}
            disabled={tokenBusy || busy || !token.trim()}
          >
            {tokenBusy ? <Spinner size={12} /> : "Save token"}
          </button>
          {masked && (
            <button
              type="button"
              className="text-xs text-cf-danger hover:underline disabled:opacity-40"
              onClick={() => void clearToken()}
              disabled={tokenBusy || busy}
            >
              clear
            </button>
          )}
        </div>
        <button
          type="button"
          className="flex items-center gap-2 rounded-md border border-cf-border px-3 py-1.5 text-sm text-cf-ink hover:bg-cf-surface disabled:opacity-40"
          onClick={() => void loadProjects()}
          disabled={loadingProjects || busy || !masked}
          title={masked ? undefined : "Save a Management token first"}
        >
          {loadingProjects && <Spinner size={12} />} Load projects
        </button>

        {projects && projects.length === 0 && (
          <p className="mt-2 text-xs text-cf-muted">No projects found for this account.</p>
        )}

        {projects && projects.length > 0 && (
          <div className="mt-3 space-y-1">
            {projects.map((p) => {
              const already = existingRefs.has(p.ref);
              return (
                <label
                  key={p.ref}
                  className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-sm ${
                    already ? "opacity-50" : "cursor-pointer hover:bg-cf-surface"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={already || !!checked[p.ref]}
                    disabled={already || busy}
                    onChange={(e) =>
                      setChecked((c) => ({ ...c, [p.ref]: e.target.checked }))
                    }
                  />
                  <span className="text-cf-ink">{p.name}</span>
                  <span className="font-mono text-xs text-cf-muted">{p.ref}</span>
                  {p.region && <span className="text-xs text-cf-muted">· {p.region}</span>}
                  {already && <span className="text-xs text-cf-success">· added</span>}
                </label>
              );
            })}
            <button
              type="button"
              className="mt-2 rounded-md bg-cf-accent px-3 py-1.5 text-sm font-medium text-cf-accent-ink hover:opacity-90 disabled:opacity-40"
              onClick={() => void addSelected()}
              disabled={busy || selectedCount === 0}
            >
              Add selected{selectedCount > 0 ? ` (${selectedCount})` : ""}
            </button>
          </div>
        )}
      </div>

      <div className="space-y-1">
        {connections.map((c) => (
          <div
            key={c.project_ref}
            className="flex items-center justify-between rounded-md bg-cf-surface-2 px-3 py-2 text-sm"
          >
            <div className="min-w-0">
              <span className="text-cf-ink">{c.name}</span>{" "}
              <span className="font-mono text-xs text-cf-muted">{c.project_ref}</span>
              {c.region && <span className="ml-2 text-xs text-cf-muted">{c.region}</span>}
              <div className="text-xs text-cf-muted">
                {c.snapshotPresent && c.lastSnapshotAt
                  ? `last snapshot ${new Date(c.lastSnapshotAt).toLocaleString()}`
                  : "no snapshot yet"}
              </div>
            </div>
            <div className="flex items-center gap-2">
              {c.snapshotPresent && (
                <IndexButton projectId={projectId} itemId={`supabase:${c.subdir}/meta.json`} disabled={busy} />
              )}
              <button
                type="button"
                className="rounded border border-cf-border px-2 py-0.5 text-xs text-cf-ink hover:bg-cf-surface disabled:opacity-40"
                disabled={busy}
                onClick={() => void runSnapshot(c.project_ref)}
              >
                {snapshotTarget === c.project_ref ? <Spinner size={10} /> : "Snapshot"}
              </button>
              <button
                type="button"
                className="text-cf-danger disabled:opacity-40"
                disabled={busy}
                onClick={() => void remove(c.project_ref)}
              >
                ✕
              </button>
            </div>
          </div>
        ))}
      </div>

      {error && <pre className="mt-3 whitespace-pre-wrap text-xs text-cf-danger">{error}</pre>}
    </div>
  );
}
