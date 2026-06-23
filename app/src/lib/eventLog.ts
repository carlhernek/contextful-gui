export interface EventLogEntry {
  raw: string;
  ts: string;
  scope: string;
  status: string;
  message: string;
}

export interface EventLogContext {
  runId?: string;
  appVersion?: string;
  modulesVersion?: string;
}

export interface EventLogEntryWithContext extends EventLogEntry {
  context: EventLogContext;
}

export interface LogFilterOptions {
  category?: LogFilter;
  from?: Date | null;
  to?: Date | null;
  appVersion?: string;
  modulesVersion?: string;
  runId?: string;
}

const LINE_RE = /^\[([^\]]+)\]\s+(\S+)\s+(\S+)(?:\s+—\s+(.*))?$/;
const RUN_ID_RE = /\brunId=([^\s)]+)/;

export function parseEventLog(text: string): EventLogEntry[] {
  const out: EventLogEntry[] = [];
  for (const raw of text.split("\n")) {
    if (!raw.trim()) continue;
    const m = LINE_RE.exec(raw);
    if (m) {
      out.push({ raw, ts: m[1], scope: m[2], status: m[3], message: m[4] ?? "" });
    } else {
      out.push({ raw, ts: "", scope: "", status: "", message: raw });
    }
  }
  return out;
}

export function parseEntryTimestamp(entry: EventLogEntry): Date | null {
  if (!entry.ts) return null;
  const ms = Date.parse(entry.ts);
  return Number.isNaN(ms) ? null : new Date(ms);
}

const APP_VERSION_RE = /\bapp=v(\d+\.\d+\.\d+)\b/;
const MODULES_VERSION_RE = /\bmodules=v?(\d+\.\d+\.\d+)\b/;
const MODULES_STATUS_RE = /\bmodules v(\d+\.\d+\.\d+)\b/;

export function extractVersions(message: string): { app?: string; modules?: string } {
  const out: { app?: string; modules?: string } = {};
  const app = APP_VERSION_RE.exec(message);
  if (app) out.app = app[1];
  const mods = MODULES_VERSION_RE.exec(message) ?? MODULES_STATUS_RE.exec(message);
  if (mods) out.modules = mods[1];
  return out;
}

export function extractRunId(message: string): string | undefined {
  return RUN_ID_RE.exec(message)?.[1];
}

/** Attach inferred runId / app / modules by carrying context forward through the log. */
export function propagateLogContext(entries: EventLogEntry[]): EventLogEntryWithContext[] {
  let runId: string | undefined;
  let appVersion: string | undefined;
  let modulesVersion: string | undefined;

  return entries.map((entry) => {
    const inline = extractVersions(entry.message);
    const inlineRun = extractRunId(entry.message);

    if (entry.scope === "run" && entry.status === "START") {
      runId = inlineRun ?? runId;
      if (inline.app) appVersion = inline.app;
      if (inline.modules) modulesVersion = inline.modules;
    } else if (inlineRun) {
      runId = inlineRun;
    }

    if (entry.scope === "modules" && inline.modules) {
      modulesVersion = inline.modules;
    }

    if (
      entry.status === "START" &&
      entry.scope !== "run" &&
      entry.scope !== "job" &&
      entry.scope !== "git"
    ) {
      if (inline.app) appVersion = inline.app;
      if (inline.modules) modulesVersion = inline.modules;
    }

    return {
      ...entry,
      context: {
        runId,
        appVersion,
        modulesVersion,
      },
    };
  });
}

export function collectDistinctVersions(entries: EventLogEntryWithContext[]): {
  app: string[];
  modules: string[];
} {
  const app = new Set<string>();
  const modules = new Set<string>();
  for (const e of entries) {
    if (e.context.appVersion) app.add(e.context.appVersion);
    if (e.context.modulesVersion) modules.add(e.context.modulesVersion);
    const v = extractVersions(e.message);
    if (v.app) app.add(v.app);
    if (v.modules) modules.add(v.modules);
  }
  return {
    app: [...app].sort(),
    modules: [...modules].sort(),
  };
}

export function collectDistinctRunIds(entries: EventLogEntryWithContext[]): string[] {
  const ids = new Set<string>();
  for (const e of entries) {
    if (e.context.runId) ids.add(e.context.runId);
  }
  return [...ids].sort().reverse();
}

function endOfMinute(d: Date): Date {
  return new Date(d.getTime() + 59_999);
}

export function filterByDateRange(
  entries: EventLogEntry[],
  from?: Date | null,
  to?: Date | null,
): EventLogEntry[] {
  if (!from && !to) return entries;
  const toInclusive = to ? endOfMinute(to) : null;
  return entries.filter((e) => {
    const ts = parseEntryTimestamp(e);
    if (!ts) return true;
    if (from && ts < from) return false;
    if (toInclusive && ts > toInclusive) return false;
    return true;
  });
}

export function filterByVersion(
  entries: EventLogEntryWithContext[],
  appVersion?: string,
  modulesVersion?: string,
): EventLogEntryWithContext[] {
  if (!appVersion && !modulesVersion) return entries;
  return entries.filter((e) => {
    if (appVersion && e.context.appVersion !== appVersion) return false;
    if (modulesVersion && e.context.modulesVersion !== modulesVersion) return false;
    return true;
  });
}

export function filterByRunId(
  entries: EventLogEntryWithContext[],
  runId?: string,
): EventLogEntryWithContext[] {
  if (!runId) return entries;
  return entries.filter((e) => e.context.runId === runId);
}

export function applyLogFilters(
  entries: EventLogEntryWithContext[],
  options: LogFilterOptions,
): EventLogEntryWithContext[] {
  let out: EventLogEntryWithContext[] = filterByDateRange(entries, options.from, options.to);
  out = filterByVersion(out, options.appVersion, options.modulesVersion);
  out = filterByRunId(out, options.runId);
  if (options.category && options.category !== "ALL") {
    out = filterEntries(out, options.category);
  }
  return out;
}

export type LogFilter = "ALL" | "Ops" | "TURN" | "ERROR" | "TOOL" | "LLM" | "INDEX" | "STATE";

const OPS_STATUSES = new Set([
  "START",
  "SUCCESS",
  "CANCELLED",
  "RETRY",
  "RESUME",
  "SKIP",
  "WARN",
  "ENUMERATE",
  "SCAN_START",
  "SCAN_DONE",
  "SCAN_ITEM",
]);
const OPS_SCOPES = new Set(["job", "git", "index", "gui", "run", "modules"]);
const INDEX_STATUSES = new Set([
  "ENUMERATE",
  "SCAN_START",
  "SCAN_DONE",
  "SCAN_ITEM",
  "INDEX_START",
  "INDEX_DONE",
  "CACHE_HIT",
  "CACHE_MISS",
  "FORCE_REINDEX",
]);

function isModuleScope(scope: string): boolean {
  return scope !== "" && !OPS_SCOPES.has(scope);
}

export function filterEntries(
  entries: EventLogEntryWithContext[],
  filter: LogFilter,
): EventLogEntryWithContext[] {
  if (filter === "ALL") return entries;
  return entries.filter((e) => {
    switch (filter) {
      case "Ops":
        return (
          OPS_SCOPES.has(e.scope) ||
          (OPS_STATUSES.has(e.status) && !isModuleScope(e.scope))
        );
      case "TURN":
        return e.status === "TURN";
      case "ERROR":
        return e.status === "ERROR";
      case "TOOL":
        return e.status === "TOOL" || e.status === "TOOL_DONE";
      case "LLM":
        return e.status === "LLM_REQUEST" || e.status === "LLM_RESPONSE";
      case "INDEX":
        return INDEX_STATUSES.has(e.status) || e.scope === "workspace-index";
      case "STATE":
        return e.status === "STATE";
      default:
        return true;
    }
  });
}

/** Keep only the last N parsed lines for snappy UI rendering. */
export function tailEventLog<T extends EventLogEntry>(entries: T[], maxLines = 2000): T[] {
  if (entries.length <= maxLines) return entries;
  return entries.slice(-maxLines);
}
