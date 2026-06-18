import { useCallback, useEffect, useState } from "react";
import { api, type VersionStatus } from "../lib/ipc";
import { Spinner } from "./Spinner";

interface Props {
  projectId: string;
  onModulesUpdated?: () => void;
}

function isBehind(status: VersionStatus): boolean {
  return (
    status.updateAvailable ||
    (status.remote !== status.local &&
      status.remote.localeCompare(status.local, undefined, { numeric: true }) > 0)
  );
}

function CheckIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 20 20" fill="currentColor" aria-hidden>
      <path
        fillRule="evenodd"
        d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
        clipRule="evenodd"
      />
    </svg>
  );
}

function DownloadIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 20 20" fill="currentColor" aria-hidden>
      <path
        fillRule="evenodd"
        d="M3 17a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm3.293-7.707a1 1 0 011.414 0L9 10.586V3a1 1 0 112 0v7.586l1.293-1.293a1 1 0 111.414 1.414l-3 3a1 1 0 01-1.414 0l-3-3a1 1 0 010-1.414z"
        clipRule="evenodd"
      />
    </svg>
  );
}

export function ModulesVersionBadge({ projectId, onModulesUpdated }: Props) {
  const [status, setStatus] = useState<VersionStatus | null>(null);
  const [checking, setChecking] = useState(true);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async (fetch: boolean) => {
    setChecking(true);
    try {
      setStatus(await api.getModulesVersionStatus(projectId, fetch));
    } catch {
      setStatus(null);
    } finally {
      setChecking(false);
    }
  }, [projectId]);

  // Startup + project switch: fetch remote version (logs to .eventlog on Rust side).
  useEffect(() => {
    void refresh(true);
  }, [refresh]);

  const pull = async () => {
    setBusy(true);
    try {
      await api.pullProjectModules(projectId);
      await refresh(false);
      onModulesUpdated?.();
    } finally {
      setBusy(false);
    }
  };

  if (checking && !status) {
    return (
      <div className="flex items-center gap-1.5 text-xs text-cf-muted">
        <Spinner size={10} />
        <span>Checking modules…</span>
      </div>
    );
  }

  if (!status) return null;

  const behind = isBehind(status);

  return (
    <div className="flex items-center gap-2 text-xs text-cf-muted">
      {behind ? (
        <DownloadIcon className="h-3.5 w-3.5 shrink-0 text-cf-warning" />
      ) : (
        <CheckIcon className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
      )}
      <span>modules v{status.local}</span>
      {behind ? (
        <button
          type="button"
          className="flex items-center gap-1 rounded-full border border-cf-warning/50 bg-cf-warning/10 px-2 py-0.5 font-medium text-cf-warning hover:bg-cf-warning/20"
          onClick={() => void pull()}
          disabled={busy || checking}
          title={`Download module pack v${status.remote}`}
        >
          {busy ? <Spinner size={10} /> : <DownloadIcon className="h-3 w-3" />}
          Update → v{status.remote}
        </button>
      ) : (
        <span className="text-emerald-600/90" title={`Up to date with remote v${status.remote}`}>
          up to date
        </span>
      )}
      <button
        type="button"
        className="underline hover:text-cf-ink disabled:opacity-50"
        onClick={() => void refresh(true)}
        disabled={checking || busy}
        title="Re-check module version against template"
      >
        {checking ? "…" : "check"}
      </button>
    </div>
  );
}
