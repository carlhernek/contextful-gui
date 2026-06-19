import { useCallback, useEffect, useState } from "react";
import { api, onContextfulEvent, type RunState } from "../lib/ipc";
import {
  deriveModuleStages,
  orderModules,
  resolvePlannedModules,
  type ModuleStage,
} from "../lib/runProgress";

const RUN_POLL_MS = 5000;

const IDLE_STATE = (runId: string): RunState => ({
  runId,
  status: "idle",
  completedModules: [],
});

export function useRunProgress(
  projectId: string,
  runId: string | null,
  artifactModuleIds: string[] = [],
  plannedFallback: string[] = [],
) {
  const [runState, setRunState] = useState<RunState | null>(null);
  const [currentModule, setCurrentModule] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!runId) {
      setRunState(null);
      setCurrentModule(null);
      return;
    }
    try {
      const s = await api.getRunState(projectId, runId);
      setRunState(s);
      if (s.status !== "running") {
        setCurrentModule(null);
      }
    } catch {
      setRunState(IDLE_STATE(runId));
    }
  }, [projectId, runId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!runId) return;
    let unlisten: (() => void) | undefined;
    onContextfulEvent((e) => {
      if (e.event === "module") {
        const d = e.data as { module: string; status: string };
        if (d.status === "START") {
          setCurrentModule(d.module);
        } else if (d.status === "SUCCESS" || d.status === "ERROR") {
          setCurrentModule((cur) => (cur === d.module ? null : cur));
        }
        void refresh();
      } else if (e.event === "run") {
        const d = e.data as { runId?: string; status?: string; completedModules?: string[] };
        if (d.runId && d.runId !== runId) return;
        setRunState((prev) =>
          prev
            ? {
                ...prev,
                status: (d.status as RunState["status"]) ?? prev.status,
                completedModules: d.completedModules ?? prev.completedModules,
              }
            : prev,
        );
        if (d.status && d.status !== "running") {
          setCurrentModule(null);
        }
        void refresh();
      }
    }).then((fn) => {
      unlisten = fn;
    });
    return () => unlisten?.();
  }, [runId, refresh]);

  useEffect(() => {
    if (runState?.status !== "running" || !runId) return;
    const timer = setInterval(() => void refresh(), RUN_POLL_MS);
    return () => clearInterval(timer);
  }, [runState?.status, runId, refresh]);

  const planned = runState
    ? resolvePlannedModules(runState, artifactModuleIds, plannedFallback)
    : plannedFallback.length
      ? orderModules(plannedFallback)
      : [];
  const stages: ModuleStage[] = runState
    ? deriveModuleStages(planned, runState, currentModule)
    : [];

  return { runState, currentModule, planned, stages, refresh };
}
