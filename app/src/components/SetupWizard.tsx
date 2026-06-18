import { useEffect, useState } from "react";
import { api, type PrereqStatus, type SetupStatus } from "../lib/ipc";
import { Spinner } from "./Spinner";

function Check({ ok, label, detail }: { ok: boolean; label: string; detail?: string | null }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className={ok ? "text-cf-success" : "text-cf-danger"}>{ok ? "✓" : "✗"}</span>
      <span className="text-cf-ink">{label}</span>
      {detail && <span className="text-cf-muted">— {detail}</span>}
    </div>
  );
}

export function SetupWizard({ onReady }: { onReady: () => void }) {
  const [prereqs, setPrereqs] = useState<PrereqStatus | null>(null);
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    setPrereqs(await api.checkPrereqs());
    setStatus(await api.getSetupStatus());
  };

  useEffect(() => {
    void refresh();
  }, []);

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setError(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const pickFolder = () =>
    run("folder", async () => {
      const picked = await api.pickInstallFolder();
      if (picked) await api.setInstallPath(picked);
    });

  const useDefault = () =>
    run("folder", async () => {
      const def = await api.defaultInstallFolder();
      await api.setInstallPath(def);
    });

  const saveKey = () =>
    run("key", async () => {
      if (apiKey.trim()) {
        await api.setApiKey(apiKey.trim());
        setApiKey("");
      }
    });

  const setupTemplate = () => run("template", async () => api.setupTemplate());

  const ready =
    !!status?.hasApiKey && !!status?.templateReady && !!status?.installPath;

  return (
    <div className="mx-auto flex min-h-full max-w-2xl flex-col gap-6 p-10">
      <div>
        <h1 className="text-2xl font-bold text-cf-ink">Welcome to Contextful</h1>
        <p className="mt-1 text-sm text-cf-muted">
          Let's get your environment ready. Complete each step below.
        </p>
      </div>

      <section className="rounded-lg border border-cf-border bg-cf-surface p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-semibold text-cf-ink">1. Prerequisites</h2>
          <button
            className="rounded-md border border-cf-border px-2 py-1 text-xs text-cf-ink hover:bg-cf-surface-2"
            onClick={() => run("recheck", refresh)}
          >
            Re-check
          </button>
        </div>
        {prereqs ? (
          <div className="space-y-1">
            <Check ok={prereqs.git} label="git" detail={prereqs.git_path} />
            <Check ok={prereqs.python} label="Python 3.12+" detail={prereqs.python_path} />
            <Check ok={prereqs.ripgrep} label="ripgrep (optional)" />
            <Check ok={prereqs.network} label="network (github.com:443)" />
            {(!prereqs.git || !prereqs.python) && (
              <button
                className="mt-2 rounded-md bg-cf-accent px-3 py-1.5 text-sm font-medium text-cf-accent-ink hover:opacity-90"
                onClick={() => run("install", api.installPrereqs)}
              >
                {busy === "install" ? <Spinner size={12} /> : "Install missing"}
              </button>
            )}
          </div>
        ) : (
          <Spinner />
        )}
      </section>

      <section className="rounded-lg border border-cf-border bg-cf-surface p-4">
        <h2 className="mb-3 font-semibold text-cf-ink">2. OpenRouter API key</h2>
        {status?.hasApiKey ? (
          <div className="flex items-center justify-between text-sm">
            <span className="text-cf-success">Saved ({status.maskedApiKey})</span>
            <button
              className="rounded-md border border-cf-border px-2 py-1 text-xs text-cf-ink hover:bg-cf-surface-2"
              onClick={() => run("key", api.clearApiKey)}
            >
              Clear
            </button>
          </div>
        ) : (
          <div className="flex gap-2">
            <input
              type="password"
              className="flex-1 rounded-md border border-cf-border bg-cf-surface-2 px-2 py-1.5 text-sm text-cf-ink outline-none focus:border-cf-accent"
              placeholder="sk-or-..."
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
            <button
              className="rounded-md bg-cf-accent px-3 py-1.5 text-sm font-medium text-cf-accent-ink hover:opacity-90"
              onClick={saveKey}
            >
              {busy === "key" ? <Spinner size={12} /> : "Save"}
            </button>
          </div>
        )}
      </section>

      <section className="rounded-lg border border-cf-border bg-cf-surface p-4">
        <h2 className="mb-3 font-semibold text-cf-ink">3. Install folder</h2>
        {status?.installPath ? (
          <p className="text-sm text-cf-success">{status.installPath}</p>
        ) : (
          <div className="flex gap-2">
            <button
              className="rounded-md border border-cf-border px-3 py-1.5 text-sm text-cf-ink hover:bg-cf-surface-2"
              onClick={pickFolder}
            >
              Choose folder…
            </button>
            <button
              className="rounded-md border border-cf-border px-3 py-1.5 text-sm text-cf-ink hover:bg-cf-surface-2"
              onClick={useDefault}
            >
              Use default
            </button>
          </div>
        )}
      </section>

      <section className="rounded-lg border border-cf-border bg-cf-surface p-4">
        <h2 className="mb-3 font-semibold text-cf-ink">4. Module templates</h2>
        {status?.templateReady ? (
          <p className="text-sm text-cf-success">Template repository cloned.</p>
        ) : (
          <button
            className="rounded-md bg-cf-accent px-3 py-1.5 text-sm font-medium text-cf-accent-ink hover:opacity-90 disabled:opacity-40"
            disabled={!status?.installPath || busy === "template"}
            onClick={setupTemplate}
          >
            {busy === "template" ? <Spinner size={12} /> : "Clone templates"}
          </button>
        )}
      </section>

      {error && <p className="text-sm text-cf-danger">{error}</p>}

      <button
        className="rounded-md bg-cf-success px-4 py-2 font-medium text-cf-bg hover:opacity-90 disabled:opacity-40"
        disabled={!ready}
        onClick={onReady}
      >
        Enter workspace
      </button>
    </div>
  );
}
