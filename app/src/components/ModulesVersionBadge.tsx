import { useEffect, useState } from "react";
import { api, type VersionStatus } from "../lib/ipc";
import { Spinner } from "./Spinner";

export function ModulesVersionBadge({ projectId }: { projectId: string }) {
  const [status, setStatus] = useState<VersionStatus | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = async (fetch: boolean) => {
    try {
      setStatus(await api.getModulesVersionStatus(projectId, fetch));
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    void refresh(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const pull = async () => {
    setBusy(true);
    try {
      await api.pullProjectModules(projectId);
      await refresh(false);
    } finally {
      setBusy(false);
    }
  };

  if (!status) return null;

  return (
    <div className="flex items-center gap-2 text-xs text-cf-muted">
      <span>modules v{status.local}</span>
      {status.updateAvailable && (
        <button
          className="flex items-center gap-1 rounded-full border border-cf-warning/50 bg-cf-warning/10 px-2 py-0.5 text-cf-warning hover:bg-cf-warning/20"
          onClick={pull}
          disabled={busy}
        >
          {busy ? <Spinner size={10} /> : null}
          update available → v{status.remote}
        </button>
      )}
      <button className="underline hover:text-cf-ink" onClick={() => refresh(true)}>
        check
      </button>
    </div>
  );
}
