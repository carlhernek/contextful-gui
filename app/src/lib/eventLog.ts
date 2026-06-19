export interface EventLogEntry {
  raw: string;
  ts: string;
  scope: string;
  status: string;
  message: string;
}

export interface LogFilterOptions {
  category?: LogFilter;
  from?: Date | null;
  to?: Date | null;
  appVersion?: string;
  modulesVersion?: string;
}

const LINE_RE = /^\[([^\]]+)\]\s+(\S+)\s+(\S+)(?:\s+—\s+(.*))?$/;

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

export function collectDistinctVersions(entries: EventLogEntry[]): {
  app: string[];
  modules: string[];
} {
  const app = new Set<string>();
  const modules = new Set<string>();
  for (const e of entries) {
    const v = extractVersions(e.message);
    if (v.app) app.add(v.app);
    if (v.modules) modules.add(v.modules);
  }
  return {
    app: [...app].sort(),
    modules: [...modules].sort(),
  };
}

export function filterByDateRange(
  entries: EventLogEntry[],
  from?: Date | null,
  to?: Date | null,
): EventLogEntry[] {
  if (!from && !to) return entries;
  return entries.filter((e) => {
    const ts = parseEntryTimestamp(e);
    if (!ts) return true;
    if (from && ts < from) return false;
    if (to && ts > to) return false;
    return true;
  });
}

export function filterByVersion(
  entries: EventLogEntry[],
  appVersion?: string,
  modulesVersion?: string,
): EventLogEntry[] {
  if (!appVersion && !modulesVersion) return entries;
  return entries.filter((e) => {
    const v = extractVersions(e.message);
    if (appVersion && v.app !== appVersion) return false;
    if (modulesVersion && v.modules !== modulesVersion) return false;
    return true;
  });
}

export function applyLogFilters(
  entries: EventLogEntry[],
  options: LogFilterOptions,
): EventLogEntry[] {
  let out = filterByDateRange(entries, options.from, options.to);
  out = filterByVersion(out, options.appVersion, options.modulesVersion);
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

export function filterEntries(entries: EventLogEntry[], filter: LogFilter): EventLogEntry[] {
  if (filter === "ALL") return entries;
  return entries.filter((e) => {
    switch (filter) {
      case "Ops":
        return OPS_STATUSES.has(e.status) || OPS_SCOPES.has(e.scope);
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
export function tailEventLog(entries: EventLogEntry[], maxLines = 2000): EventLogEntry[] {
  if (entries.length <= maxLines) return entries;
  return entries.slice(-maxLines);
}
