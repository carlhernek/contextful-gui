import { describe, expect, it } from "vitest";
import {
  applyLogFilters,
  filterEntries,
  parseEventLog,
  propagateLogContext,
} from "./eventLog";

const SAMPLE = `[2026-06-22T10:40:29+02:00] run START — runId=20260622-104028-b5aa app=v1.2.18 modules=1.3.2 (1 to run)
[2026-06-22T10:40:29+02:00] workspace-index START — modules=1.3.2 app=v1.2.18
[2026-06-22T10:40:32+02:00] workspace-index TOOL — repo:API gather_context {"path": "repos/API"}
[2026-06-22T10:40:32+02:00] workspace-index TOOL_DONE — gather_context: # Context bundle (13738 bytes, 16ms)
[2026-06-22T10:40:37+02:00] workspace-index SUCCESS — indexed API
[2026-06-22T10:40:37+02:00] run SUCCESS — runId=20260622-104028-b5aa completed 1 modules
[2026-06-22T10:44:35+02:00] run START — runId=20260622-104435-3949 app=v1.2.18 modules=1.3.2 (5 to run)
[2026-06-22T10:44:35+02:00] accessibility-pass START — modules=1.3.2 app=v1.2.18
[2026-06-22T10:44:39+02:00] accessibility-pass TOOL — gather_context {"path": "repos/backoffice"}
[2026-06-22T10:44:39+02:00] accessibility-pass LLM_REQUEST — model=deepseek/deepseek-v4-flash messages=2 toolDefs=11
[2026-06-22T10:56:01+02:00] accessibility-pass SUCCESS — ## Accessibility Pass — Complete
[2026-06-22T10:23:12+02:00] job START — Cloning repositories (clone)
[2026-06-22T10:23:16+02:00] git ERROR — API clone failed — terminal prompts disabled`;

describe("propagateLogContext", () => {
  it("carries app/modules/runId to granular lines", () => {
    const ctx = propagateLogContext(parseEventLog(SAMPLE));
    const tool = ctx.find((e) => e.status === "TOOL" && e.scope === "accessibility-pass");
    expect(tool?.context.appVersion).toBe("1.2.18");
    expect(tool?.context.modulesVersion).toBe("1.3.2");
    expect(tool?.context.runId).toBe("20260622-104435-3949");

    const indexTool = ctx.find((e) => e.status === "TOOL" && e.scope === "workspace-index");
    expect(indexTool?.context.runId).toBe("20260622-104028-b5aa");
  });
});

describe("filterByVersion", () => {
  it("keeps TOOL/LLM lines for selected app version", () => {
    const ctx = propagateLogContext(parseEventLog(SAMPLE));
    const filtered = applyLogFilters(ctx, { appVersion: "1.2.18", category: "TOOL" });
    expect(filtered.length).toBeGreaterThan(0);
    expect(filtered.every((e) => e.status === "TOOL" || e.status === "TOOL_DONE")).toBe(true);
    expect(filtered.some((e) => e.scope === "accessibility-pass")).toBe(true);
  });
});

describe("filterByRunId", () => {
  it("isolates a single pipeline run", () => {
    const ctx = propagateLogContext(parseEventLog(SAMPLE));
    const filtered = applyLogFilters(ctx, { runId: "20260622-104435-3949" });
    expect(filtered.some((e) => e.scope === "accessibility-pass")).toBe(true);
    expect(filtered.some((e) => e.scope === "workspace-index")).toBe(false);
  });
});

describe("filterEntries Ops", () => {
  it("excludes module SUCCESS but keeps git/job/run", () => {
    const ctx = propagateLogContext(parseEventLog(SAMPLE));
    const ops = filterEntries(ctx, "Ops");
    expect(ops.some((e) => e.scope === "git" && e.status === "ERROR")).toBe(true);
    expect(ops.some((e) => e.scope === "job")).toBe(true);
    expect(ops.some((e) => e.scope === "accessibility-pass" && e.status === "SUCCESS")).toBe(
      false,
    );
    expect(ops.some((e) => e.scope === "run" && e.status === "START")).toBe(true);
  });
});

describe("filterByDateRange", () => {
  it("includes entries within Until minute boundary", () => {
    const line =
      "[2026-06-22T10:40:32+02:00] workspace-index TOOL — gather_context";
    const ctx = propagateLogContext(parseEventLog(line));
    const to = new Date("2026-06-22T10:40:00");
    const filtered = applyLogFilters(ctx, { to });
    expect(filtered.length).toBe(1);
  });
});
