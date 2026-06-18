import { useCallback, useEffect, useState } from "react";
import { api, onContextfulEvent, type RunState } from "../lib/ipc";
import { statusTextClass } from "../lib/statusStyles";

interface Props {
  projectId: string;
  activeRunId: string | null;
  refreshKey: number;
  onSelect: (runId: string) => void;
}

const RUN_POLL_MS = 5000;

export function RunHistory({ projectId, activeRunId, refreshKey, onSelect }: Props) {
  const [runs, setRuns] = useState<RunState[]>([]);

  const refresh = useCallback(async () => {
    try {
      setRuns(await api.listRuns(projectId));
    } catch {
      setRuns([]);
    }
  }, [projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh, refreshKey]);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    onContextfulEvent((e) => {
      if (e.event === "run" || e.event === "module" || e.event === "job") {
        void refresh();
      }
    }).then((fn) => (unlisten = fn));
    return () => unlisten?.();
  }, [refresh]);

  useEffect(() => {
    const hasRunning = runs.some((r) => r.status === "running");
    if (!hasRunning) return;
    const timer = setInterval(() => void refresh(), RUN_POLL_MS);
    return () => clearInterval(timer);
  }, [runs, refresh]);

  return (
    <div className="rounded-lg border border-cf-border bg-cf-surface p-4">
      <h3 className="mb-3 font-semibold text-cf-ink">Run history</h3>
      {runs.length === 0 && <p className="text-sm text-cf-muted">No runs yet.</p>}
      <div className="space-y-1">
        {runs.map((r) => (
          <button
            key={r.runId}
            className={`flex w-full items-center justify-between rounded-md px-3 py-1.5 text-sm ${
              r.runId === activeRunId ? "bg-cf-surface-2" : "hover:bg-cf-surface-2/50"
            }`}
            onClick={() => onSelect(r.runId)}
          >
            <span className="font-mono text-xs text-cf-ink">{r.runId}</span>
            <span className={statusTextClass(r.status)}>{r.status}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
