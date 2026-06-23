import { useCallback } from "react";
import { api, type RunState } from "../lib/ipc";
import { useJob } from "../lib/jobs";
import { modulesForResume } from "../lib/runProgress";

export function useResumeRun(
  projectId: string,
  onStarted?: (runId: string) => void,
  onComplete?: (runId: string) => void,
) {
  const { busy: runBusy, isBusy } = useJob("run", projectId);

  const resume = useCallback(
    async (state: RunState) => {
      if (isBusy) return;
      const modules = modulesForResume(state);
      if (!modules.length) return;
      onStarted?.(state.runId);
      const result = await api.startRun({
        id: projectId,
        runId: state.runId,
        modules,
        force: false,
        forceReindex: false,
        resume: true,
      });
      onComplete?.(state.runId);
      return result;
    },
    [projectId, isBusy, onStarted, onComplete],
  );

  return { resume, runBusy };
}
