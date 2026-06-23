import { describe, expect, it } from "vitest";
import { canResumeRun, modulesForResume, resolvePlannedModules } from "./runProgress";
import type { RunState } from "./ipc";

function state(partial: Partial<RunState> & { runId: string }): RunState {
  return {
    status: "idle",
    completedModules: [],
    ...partial,
  };
}

describe("canResumeRun", () => {
  it("returns true when failed with remaining planned modules", () => {
    const s = state({
      runId: "r1",
      status: "failed",
      plannedModules: ["a", "b"],
      completedModules: ["a"],
      failedModule: "b",
    });
    expect(canResumeRun(s)).toBe(true);
    expect(resolvePlannedModules(s)).toEqual(["a", "b"]);
  });

  it("returns true for cancelled runs with pending modules", () => {
    const s = state({
      runId: "r2",
      status: "cancelled",
      plannedModules: ["mod"],
      completedModules: [],
    });
    expect(canResumeRun(s)).toBe(true);
  });

  it("returns false when all planned modules completed", () => {
    const s = state({
      runId: "r3",
      status: "failed",
      plannedModules: ["a"],
      completedModules: ["a"],
    });
    expect(canResumeRun(s)).toBe(false);
  });

  it("returns false for complete or running runs", () => {
    expect(canResumeRun(state({ runId: "r4", status: "complete" }))).toBe(false);
    expect(canResumeRun(state({ runId: "r5", status: "running" }))).toBe(false);
  });

  it("returns true when failedModule is set even without plannedModules", () => {
    const s = state({
      runId: "r6",
      status: "failed",
      failedModule: "b2b-low-hanging-features",
    });
    expect(canResumeRun(s)).toBe(true);
    expect(modulesForResume(s)).toEqual(["b2b-low-hanging-features"]);
  });
});
