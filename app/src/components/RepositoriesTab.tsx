import { useEffect, useState } from "react";
import { api, type RepoStatus } from "../lib/ipc";
import { useJob } from "../lib/jobs";
import { IndexButton } from "./IndexButton";
import { Spinner } from "./Spinner";

export function RepositoriesTab({ projectId }: { projectId: string }) {
  const [repos, setRepos] = useState<RepoStatus[]>([]);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [branch, setBranch] = useState("develop");
  const [pullTarget, setPullTarget] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { busy: cloneBusy } = useJob("clone", projectId);
  const { busy: pullBusy } = useJob("pull", projectId);
  const { isBusy } = useJob(undefined, projectId);
  const repoBusy = cloneBusy || pullBusy || isBusy;

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

  const runClone = async () => {
    setError(null);
    try {
      const res = await api.cloneRepos(projectId);
      reportFailures(res.results as { ok: boolean; error?: string; kind?: string }[]);
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  const runPull = async (repoName?: string) => {
    setPullTarget(repoName ?? "pull-all");
    setError(null);
    try {
      const res = await api.pullRepos(projectId);
      const results = res.results as { name: string; ok: boolean; error?: string; kind?: string }[];
      const filtered = repoName ? results.filter((r) => r.name === repoName) : results;
      reportFailures(filtered);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setPullTarget(null);
    }
  };

  const reportFailures = (results: { ok: boolean; error?: string; kind?: string }[]) => {
    const failed = results.filter((r) => !r.ok);
    if (failed.length) {
      setError(
        failed
          .map((f) =>
            f.kind === "auth"
              ? `Auth failed — set up system git auth and retry. ${f.error}`
              : f.error
          )
          .join("\n")
      );
    }
  };

  return (
    <div className="mx-auto max-w-4xl rounded-lg border border-cf-border bg-cf-surface p-4">
      <div className="mb-3 rounded-md border border-cf-border bg-cf-surface-2 px-3 py-2 text-xs text-cf-muted">
        Target repositories are read-only mirrors — push is disabled.
      </div>

      <h3 className="mb-3 font-semibold text-cf-ink">Project repositories</h3>

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
          type="button"
          className="rounded-md border border-cf-border px-3 text-sm text-cf-ink hover:bg-cf-surface-2"
          onClick={() => void add()}
          disabled={repoBusy}
        >
          Add
        </button>
      </div>

      <div className="space-y-1">
        {repos.map((r) => (
          <div
            key={r.name}
            className="flex items-center justify-between rounded-md bg-cf-surface-2 px-3 py-2 text-sm"
          >
            <div className="min-w-0">
              <span className="text-cf-ink">{r.name}</span>{" "}
              <span className="text-cf-muted">({r.branch})</span>
              {r.head && <span className="ml-2 text-xs text-cf-info">@{r.head}</span>}
              <div className="truncate text-xs text-cf-muted">{r.url}</div>
            </div>
            <div className="flex items-center gap-2">
              <IndexButton projectId={projectId} itemId={`repo:${r.name}`} disabled={repoBusy} />
              <span className={r.cloned ? "text-cf-success" : "text-cf-muted"}>
                {r.cloned ? "cloned" : "not cloned"}
              </span>
              {r.cloned && (
                <button
                  type="button"
                  className="rounded border border-cf-border px-2 py-0.5 text-xs text-cf-ink hover:bg-cf-surface"
                  disabled={repoBusy}
                  onClick={() => void runPull(r.name)}
                >
                  {pullTarget === r.name ? <Spinner size={10} /> : `Pull origin/${r.branch}`}
                </button>
              )}
              <button
                type="button"
                className="text-cf-danger"
                disabled={repoBusy}
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
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            className="flex items-center gap-2 rounded-md bg-cf-accent px-3 py-1.5 text-sm font-medium text-cf-accent-ink hover:opacity-90 disabled:opacity-40"
            onClick={() => void runClone()}
            disabled={repoBusy}
          >
            {cloneBusy && <Spinner size={12} />} Clone all
          </button>
          <button
            type="button"
            className="flex items-center gap-2 rounded-md border border-cf-border px-3 py-1.5 text-sm text-cf-ink hover:bg-cf-surface-2 disabled:opacity-40"
            onClick={() => void runPull()}
            disabled={repoBusy}
          >
            {pullTarget === "pull-all" && <Spinner size={12} />} Pull all
          </button>
        </div>
      )}

      {error && <pre className="mt-3 whitespace-pre-wrap text-xs text-cf-danger">{error}</pre>}
    </div>
  );
}
