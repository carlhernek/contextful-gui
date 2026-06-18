export interface EventLogEntry {
  raw: string;
  ts: string;
  scope: string;
  status: string;
  message: string;
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
]);
const OPS_SCOPES = new Set(["job", "git", "index", "gui", "run", "modules"]);
const INDEX_STATUSES = new Set([
  "ENUMERATE",
  "SCAN_START",
  "SCAN_DONE",
  "INDEX_START",
  "INDEX_DONE",
  "CACHE_HIT",
  "CACHE_MISS",
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
