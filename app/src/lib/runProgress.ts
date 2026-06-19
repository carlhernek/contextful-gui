import type { RunState } from "./ipc";

export const WORKSPACE_INDEX = "workspace-index";

export type ModuleStageStatus = "complete" | "running" | "pending" | "failed" | "skipped";

export interface ModuleStage {
  id: string;
  status: ModuleStageStatus;
}

/** Mirror sidecar `_order_modules`: workspace-index runs last. */
export function orderModules(modules: string[]): string[] {
  const regular = modules.filter((m) => m !== WORKSPACE_INDEX);
  if (modules.includes(WORKSPACE_INDEX)) {
    regular.push(WORKSPACE_INDEX);
  }
  return regular;
}

/** Merge artifact module ids with completed list for legacy runs missing plannedModules. */
export function resolvePlannedModules(
  state: RunState,
  artifactModuleIds: string[] = [],
  plannedFallback: string[] = [],
): string[] {
  if (state.plannedModules?.length) {
    return state.plannedModules;
  }
  if (plannedFallback.length) {
    return orderModules(plannedFallback);
  }
  const seen = new Set<string>();
  const out: string[] = [];
  for (const id of [...state.completedModules, ...artifactModuleIds]) {
    if (!seen.has(id)) {
      seen.add(id);
      out.push(id);
    }
  }
  return orderModules(out);
}

export function deriveModuleStages(
  planned: string[],
  state: RunState,
  currentModule: string | null,
): ModuleStage[] {
  const runEnded = state.status === "failed" || state.status === "cancelled";
  const completed = new Set(state.completedModules);

  return planned.map((id) => {
    if (state.failedModule === id) {
      return { id, status: "failed" as const };
    }
    if (completed.has(id)) {
      return { id, status: "complete" as const };
    }
    if (state.status === "running" && currentModule === id) {
      return { id, status: "running" as const };
    }
    if (runEnded) {
      return { id, status: "skipped" as const };
    }
    return { id, status: "pending" as const };
  });
}

export function completedModuleCount(stages: ModuleStage[]): number {
  return stages.filter((s) => s.status === "complete").length;
}
