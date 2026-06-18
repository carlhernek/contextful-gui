import { useEffect, useState } from "react";
import { api, type RepoStatus } from "../lib/ipc";
import { Spinner } from "./Spinner";

export function RepoManager({ projectId }: { projectId: string }) {
  const [repos, setRepos] = useState<RepoStatus[]>([]);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [branch, setBranch] = useState("main");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => setRepos(await api.listRepos(projectId));

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const add = async () => {
    if (!name.trim() || !url.trim()) return;
    setError(null);
    try {
      await api.addRepo(projectId, name.trim(), url.trim(), branch.trim() || "main");
      setName("");
      setUrl("");
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  const clone = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.cloneRepos(projectId);
      const failed = (res.results as { ok: boolean; error?: string; kind?: string }[]).filter(
        (r) => !r.ok
      );
      if (failed.length) {
        setError(
          failed
            .map((f) =>
              f.kind === "auth"
                ? `Auth failed — set up system git auth (gh auth login / SSH keys) and retry. ${f.error}`
                : f.error
            )
            .join("\n")
        );
      }
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-lg border border-cf-border bg-cf-surface p-4">
      <h3 className="mb-3 font-semibold text-cf-ink">Target repositories</h3>

      <div className="mb-3 grid grid-cols-[1fr_2fr_auto_auto] gap-2">
        <input
          className="rounded-md border border-cf-border bg-cf-surface-2 px-2 py-1.5 text-sm text-cf-ink outline-none focus:border-cf-accent"
          placeholder="name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <input
          className="rounded-md border border-cf-border bg-cf-surface-2 px-2 py-1.5 text-sm text-cf-ink outline-none focus:border-cf-accent"
          placeholder="git@github.com:org/repo.git"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <input
          className="w-24 rounded-md border border-cf-border bg-cf-surface-2 px-2 py-1.5 text-sm text-cf-ink outline-none focus:border-cf-accent"
          placeholder="branch"
          value={branch}
          onChange={(e) => setBranch(e.target.value)}
        />
        <button
          className="rounded-md border border-cf-border px-3 text-sm text-cf-ink hover:bg-cf-surface-2"
          onClick={add}
        >
          Add
        </button>
      </div>

      <div className="space-y-1">
        {repos.map((r) => (
          <div
            key={r.name}
            className="flex items-center justify-between rounded-md bg-cf-surface-2 px-3 py-1.5 text-sm"
          >
            <div className="min-w-0">
              <span className="text-cf-ink">{r.name}</span>{" "}
              <span className="text-cf-muted">({r.branch})</span>
              <div className="truncate text-xs text-cf-muted">{r.url}</div>
            </div>
            <div className="flex items-center gap-3">
              <span className={r.cloned ? "text-cf-success" : "text-cf-muted"}>
                {r.cloned ? "cloned" : "not cloned"}
              </span>
              <button
                className="text-cf-danger"
                onClick={async () => {
                  await api.removeRepo(projectId, r.name);
                  await refresh();
                }}
              >
                ✕
              </button>
            </div>
          </div>
        ))}
      </div>

      {repos.length > 0 && (
        <button
          className="mt-3 flex items-center gap-2 rounded-md bg-cf-accent px-3 py-1.5 text-sm font-medium text-cf-accent-ink hover:opacity-90"
          onClick={clone}
          disabled={busy}
        >
          {busy && <Spinner size={12} />} Clone all
        </button>
      )}

      {error && <pre className="mt-3 whitespace-pre-wrap text-xs text-cf-danger">{error}</pre>}
    </div>
  );
}
