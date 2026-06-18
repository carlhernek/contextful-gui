export type RunStatus = "idle" | "running" | "failed" | "cancelled" | "complete";

export function statusTextClass(status: string): string {
  switch (status) {
    case "complete":
    case "SUCCESS":
      return "text-cf-success";
    case "running":
    case "TURN":
      return "text-cf-info";
    case "failed":
    case "ERROR":
      return "text-cf-danger";
    case "cancelled":
    case "CANCELLED":
      return "text-cf-warning";
    default:
      return "text-cf-muted";
  }
}

export function statusBannerClass(status: string): string {
  switch (status) {
    case "complete":
      return "bg-cf-success/10 border-cf-success/40 text-cf-success";
    case "running":
      return "bg-cf-info/10 border-cf-info/40 text-cf-info";
    case "failed":
      return "bg-cf-danger/10 border-cf-danger/40 text-cf-danger";
    case "cancelled":
      return "bg-cf-warning/10 border-cf-warning/40 text-cf-warning";
    default:
      return "bg-cf-surface-2 border-cf-border text-cf-muted";
  }
}

export function statusMessageClass(status: string): string {
  return `text-sm ${statusTextClass(status)}`;
}

/** Color-code an event-log line by its status token. */
export function eventLogLineClass(line: string): string {
  if (/\sERROR\b/.test(line)) return "text-cf-danger";
  if (/\sSUCCESS\b/.test(line)) return "text-cf-success";
  if (/\sCANCELLED\b/.test(line)) return "text-cf-warning";
  if (/\sRETRY\b/.test(line)) return "text-cf-warning";
  if (/\s(TURN|RESUME)\b/.test(line)) return "text-cf-info";
  if (/\s(TOOL|TOOL_DONE)\b/.test(line)) return "text-cf-muted";
  return "text-cf-ink";
}

export const priorityClass: Record<string, string> = {
  high: "bg-cf-danger/15 text-cf-danger border-cf-danger/40",
  medium: "bg-cf-warning/15 text-cf-warning border-cf-warning/40",
  low: "bg-cf-info/15 text-cf-info border-cf-info/40",
};
