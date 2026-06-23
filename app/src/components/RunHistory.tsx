import { useCallback, useEffect, useState } from "react";
import { api, onContextfulEvent, type RunState } from "../lib/ipc";
import {
  completedModuleCount,
  deriveModuleStages,
  resolvePlannedModules,
  canResumeRun,
} from "../lib/runProgress";
import { statusTextClass } from "../lib/statusStyles";
import { useJob } from "../lib/jobs";

interface Props {
  projectId: string;
  activeRunId: string | null;
  refreshKey: number;
  onSelect: (runId: string) => void;
  onResume?: (run: RunState) => void;
}

const RUN_POLL_MS = 5000;

export function RunHistory({ projectId, activeRunId, refreshKey, onSelect, onResume }: Props) {
  const [runs, setRuns] = useState<RunState[]>([]);
  const { busy: runBusy } = useJob("run", projectId);

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
        {runs.map((r) => {
          const planned = resolvePlannedModules(r);
          const stages = deriveModuleStages(planned, r, null);
          const showProgress = r.status === "running" && stages.length > 1;
          const done = completedModuleCount(stages);
          const resumable = canResumeRun(r) && onResume;

          return (
            <div
              key={r.runId}
              className={`flex w-full items-center justify-between gap-2 rounded-md px-3 py-1.5 text-sm ${
                r.runId === activeRunId ? "bg-cf-surface-2" : "hover:bg-cf-surface-2/50"
              }`}
            >
              <button
                type="button"
                className="flex min-w-0 flex-1 flex-col text-left"
                onClick={() => onSelect(r.runId)}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-xs text-cf-ink">{r.runId}</span>
                  <span className={`shrink-0 ${statusTextClass(r.status)}`}>{r.status}</span>
                </div>
                {showProgress && (
                  <span className="text-left text-xs text-cf-muted">
                    {done}/{stages.length} modules
                  </span>
                )}
              </button>
              {resumable && (
                <button
                  type="button"
                  disabled={runBusy}
                  className="shrink-0 rounded bg-cf-success px-2 py-0.5 text-xs font-medium text-cf-bg hover:opacity-90 disabled:opacity-40"
                  onClick={() => onResume(r)}
                >
                  Resume run
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
