import { useEffect, useMemo, useState } from "react";
import { api, type GitCredentialHost, type RepoStatus } from "../lib/ipc";
import { missingPatHosts } from "../lib/gitRepoAuth";
import { useJob } from "../lib/jobs";
import { IndexButton } from "./IndexButton";
import { Spinner } from "./Spinner";
import { SupabaseConnectionsPanel } from "./SupabaseConnectionsPanel";

export function ConnectionsTab({ projectId }: { projectId: string }) {
  const [repos, setRepos] = useState<RepoStatus[]>([]);
  const [credHosts, setCredHosts] = useState<GitCredentialHost[]>([]);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [branch, setBranch] = useState("develop");
  const [pullTarget, setPullTarget] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [credHost, setCredHost] = useState("dev.azure.com");
  const [credUsername, setCredUsername] = useState("");
  const [credToken, setCredToken] = useState("");
  const [credBusy, setCredBusy] = useState(false);
  const { busy: cloneBusy } = useJob("clone", projectId);
  const { busy: pullBusy } = useJob("pull", projectId);
  const { isBusy } = useJob(undefined, projectId);
  const repoBusy = cloneBusy || pullBusy || isBusy;

  const missingHosts = useMemo(
    () =>
      missingPatHosts(
        repos,
        credHosts.filter((h) => h.configured).map((h) => h.host),
      ),
    [repos, credHosts],
  );
  const cloneBlocked = missingHosts.length > 0;

  const refresh = async () => {
    setRepos(await api.listRepos(projectId));
    const creds = await api.listGitCredentialHosts(projectId);
    setCredHosts(creds.hosts);
    if (creds.hosts.length > 0 && !creds.hosts.some((h) => h.host === credHost)) {
      setCredHost(creds.hosts[0].host);
    }
  };

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  useEffect(() => {
    const current = credHosts.find((h) => h.host === credHost);
    setCredUsername(current?.username ?? "");
  }, [credHost, credHosts]);

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

  const saveCredential = async () => {
    const host = credHost.trim();
    const token = credToken.trim();
    if (!host || !token) return;
    setCredBusy(true);
    setError(null);
    try {
      await api.setGitCredential(
        host,
        token,
        credUsername.trim() || undefined,
      );
      setCredToken("");
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setCredBusy(false);
    }
  };

  const clearCredential = async (host: string) => {
    setCredBusy(true);
    setError(null);
    try {
      await api.clearGitCredential(host);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setCredBusy(false);
    }
  };

  const reportFailures = (results: { ok: boolean; error?: string; kind?: string }[]) => {
    const failed = results.filter((r) => !r.ok);
    if (failed.length) {
      setError(
        failed
          .map((f) =>
            f.kind === "auth"
              ? `Authentication failed — re-save Git credentials for dev.azure.com (password from Generate Git Credentials + your Azure username). ${f.error ?? ""}`
              : f.error
          )
          .join("\n")
      );
    }
  };

  return (
    <div className="mx-auto max-w-4xl rounded-lg border border-cf-border bg-cf-surface p-4">
      <h3 className="mb-3 font-semibold text-cf-ink">Git repositories</h3>
      <div className="mb-3 rounded-md border border-cf-border bg-cf-surface-2 px-3 py-2 text-xs text-cf-muted">
        Target repositories are read-only mirrors — push is disabled. Private HTTPS remotes (e.g.
        Azure DevOps) need a Personal Access Token stored below; SSH remotes use your system SSH
        keys.
      </div>

      <h4 className="mb-2 text-sm font-semibold text-cf-ink">Git credentials (HTTPS)</h4>
      <div className="mb-4 rounded-md border border-cf-border bg-cf-surface-2 p-3">
        <p className="mb-2 text-xs text-cf-muted">
          Tokens are stored in the OS keychain. For Azure DevOps, click <strong>Generate Git
          Credentials</strong> on the clone page, then save the <strong>password</strong> below and
          your Azure <strong>username</strong> (e.g. <span className="font-mono">carl.hernek</span>
          ). The org name in repo URLs is separate — pull needs your personal username when Azure
          generated the password.
        </p>
        {missingHosts.length > 0 && (
          <p className="mb-2 text-xs text-cf-danger">
            No token saved for: {missingHosts.join(", ")} — clone will fail until you save a PAT
            for each host.
          </p>
        )}
        <div className="mb-2 flex flex-wrap gap-2">
          <input
            className="min-w-[10rem] rounded-md border border-cf-border bg-cf-surface px-2 py-1.5 font-mono text-sm text-cf-ink"
            placeholder="dev.azure.com"
            value={credHost}
            onChange={(e) => setCredHost(e.target.value)}
            disabled={credBusy || repoBusy}
            list="git-cred-hosts"
          />
          <datalist id="git-cred-hosts">
            {credHosts.map((h) => (
              <option key={h.host} value={h.host} />
            ))}
          </datalist>
          <input
            className="min-w-[8rem] rounded-md border border-cf-border bg-cf-surface px-2 py-1.5 font-mono text-sm text-cf-ink"
            placeholder="Azure username"
            value={credUsername}
            onChange={(e) => setCredUsername(e.target.value)}
            disabled={credBusy || repoBusy}
          />
          <input
            type="password"
            className="min-w-[12rem] flex-1 rounded-md border border-cf-border bg-cf-surface px-2 py-1.5 text-sm text-cf-ink"
            placeholder="Password / PAT from Generate Git Credentials"
            value={credToken}
            onChange={(e) => setCredToken(e.target.value)}
            disabled={credBusy || repoBusy}
          />
          <button
            type="button"
            className="rounded-md bg-cf-accent px-3 py-1.5 text-sm font-medium text-cf-accent-ink hover:opacity-90 disabled:opacity-40"
            onClick={() => void saveCredential()}
            disabled={credBusy || repoBusy || !credHost.trim() || !credToken.trim()}
          >
            {credBusy ? <Spinner size={12} /> : "Save token"}
          </button>
        </div>
        {credHosts.some((h) => h.configured) && (
          <div className="flex flex-wrap gap-2 text-xs text-cf-muted">
            {credHosts
              .filter((h) => h.configured)
              .map((h) => (
                <span key={h.host} className="inline-flex items-center gap-1">
                  {h.host}
                  {h.username ? ` (${h.username})` : ""}: {h.masked ?? "saved"}
                  <button
                    type="button"
                    className="text-cf-danger hover:underline"
                    disabled={credBusy || repoBusy}
                    onClick={() => void clearCredential(h.host)}
                  >
                    clear
                  </button>
                </span>
              ))}
          </div>
        )}
      </div>

      <h4 className="mb-3 text-sm font-semibold text-cf-ink">Project repositories</h4>

      <div className="mb-3 grid grid-cols-[1fr_2fr_auto_auto] gap-2">
        <input
          className="rounded-md border border-cf-border bg-cf-surface-2 px-2 py-1.5 text-sm text-cf-ink outline-none focus:border-cf-accent"
          placeholder="name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <input
          className="rounded-md border border-cf-border bg-cf-surface-2 px-2 py-1.5 text-sm text-cf-ink outline-none focus:border-cf-accent"
          placeholder="https://dev.azure.com/org/project/_git/repo"
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
            disabled={repoBusy || cloneBlocked}
            title={
              cloneBlocked
                ? `Save a PAT for ${missingHosts.join(", ")} before cloning`
                : undefined
            }
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

      <SupabaseConnectionsPanel projectId={projectId} />
    </div>
  );
}
