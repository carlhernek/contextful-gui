import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, onContextfulEvent } from "../lib/ipc";
import {
  parseEventLog,
  propagateLogContext,
  applyLogFilters,
  collectDistinctVersions,
  collectDistinctRunIds,
  tailEventLog,
  type LogFilter,
} from "../lib/eventLog";
import { eventLogLineClass } from "../lib/statusStyles";

const FILTERS: LogFilter[] = ["ALL", "Ops", "TURN", "LLM", "TOOL", "INDEX", "STATE", "ERROR"];
const REFRESH_MS = 10_000;
const STICKY_THRESHOLD_PX = 48;
const TAIL_LINES = 2000;

function toDatetimeLocal(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fromDatetimeLocal(value: string): Date | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function EventLogPanel({ projectId }: { projectId: string }) {
  const [text, setText] = useState("");
  const [filter, setFilter] = useState<LogFilter>("ALL");
  const [fromLocal, setFromLocal] = useState("");
  const [toLocal, setToLocal] = useState("");
  const [appVersion, setAppVersion] = useState("");
  const [modulesVersion, setModulesVersion] = useState("");
  const [runId, setRunId] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  const refresh = useCallback(async () => {
    setText(await api.getEventLog(projectId));
  }, [projectId]);

  useEffect(() => {
    void refresh();
    const t = setInterval(() => void refresh(), REFRESH_MS);
    let unlisten: (() => void) | undefined;
    onContextfulEvent((e) => {
      if (
        e.event === "job" ||
        e.event === "run" ||
        e.event === "module" ||
        e.event === "index" ||
        e.event === "activity"
      ) {
        void refresh();
      }
    }).then((fn) => {
      unlisten = fn;
    });
    return () => {
      clearInterval(t);
      unlisten?.();
    };
  }, [refresh]);

  const contextual = useMemo(() => propagateLogContext(parseEventLog(text)), [text]);
  const versions = useMemo(() => collectDistinctVersions(contextual), [contextual]);
  const runIds = useMemo(() => collectDistinctRunIds(contextual), [contextual]);

  const { entries, filteredCount } = useMemo(() => {
    const filtered = applyLogFilters(contextual, {
      category: filter,
      from: fromDatetimeLocal(fromLocal),
      to: fromDatetimeLocal(toLocal),
      appVersion: appVersion || undefined,
      modulesVersion: modulesVersion || undefined,
      runId: runId || undefined,
    });
    return {
      entries: tailEventLog(filtered, TAIL_LINES),
      filteredCount: filtered.length,
    };
  }, [contextual, filter, fromLocal, toLocal, appVersion, modulesVersion, runId]);

  const tailTruncated = filteredCount > TAIL_LINES;

  useEffect(() => {
    const el = scrollRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [entries.length]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICKY_THRESHOLD_PX;
  };

  const copyFiltered = () => {
    void navigator.clipboard.writeText(entries.map((e) => e.raw).join("\n"));
  };

  const setPreset = (preset: "hour" | "today" | "clear") => {
    if (preset === "clear") {
      setFromLocal("");
      setToLocal("");
      return;
    }
    const now = new Date();
    if (preset === "hour") {
      setFromLocal(toDatetimeLocal(new Date(now.getTime() - 60 * 60 * 1000)));
      setToLocal(toDatetimeLocal(now));
    } else {
      const start = new Date(now);
      start.setHours(0, 0, 0, 0);
      setFromLocal(toDatetimeLocal(start));
      setToLocal(toDatetimeLocal(now));
    }
  };

  return (
    <div className="flex h-full min-w-0 flex-col overflow-hidden rounded-lg border border-cf-border bg-cf-surface">
      <div className="border-b border-cf-border px-3 py-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap gap-1">
            {FILTERS.map((f) => (
              <button
                key={f}
                className={`rounded-full px-2 py-0.5 text-xs ${
                  filter === f
                    ? "bg-cf-accent text-cf-accent-ink"
                    : "text-cf-muted hover:text-cf-ink"
                }`}
                onClick={() => setFilter(f)}
              >
                {f}
              </button>
            ))}
          </div>
          <div className="flex gap-2">
            <button className="text-xs text-cf-muted hover:text-cf-ink" onClick={() => void refresh()}>
              refresh
            </button>
            <button className="text-xs text-cf-muted hover:text-cf-ink" onClick={copyFiltered}>
              copy
            </button>
          </div>
        </div>
        <div className="mt-2 flex flex-wrap items-end gap-2 text-xs">
          <label className="text-cf-muted">
            Since
            <input
              type="datetime-local"
              className="ml-1 rounded border border-cf-border bg-cf-surface-2 px-1 py-0.5 text-cf-ink"
              value={fromLocal}
              onChange={(e) => setFromLocal(e.target.value)}
            />
          </label>
          <label className="text-cf-muted">
            Until
            <input
              type="datetime-local"
              className="ml-1 rounded border border-cf-border bg-cf-surface-2 px-1 py-0.5 text-cf-ink"
              value={toLocal}
              onChange={(e) => setToLocal(e.target.value)}
            />
          </label>
          <button
            type="button"
            className="rounded border border-cf-border px-2 py-0.5 text-cf-muted hover:text-cf-ink"
            onClick={() => setPreset("hour")}
          >
            Last hour
          </button>
          <button
            type="button"
            className="rounded border border-cf-border px-2 py-0.5 text-cf-muted hover:text-cf-ink"
            onClick={() => setPreset("today")}
          >
            Today
          </button>
          <button
            type="button"
            className="rounded border border-cf-border px-2 py-0.5 text-cf-muted hover:text-cf-ink"
            onClick={() => setPreset("clear")}
          >
            Clear
          </button>
          <label className="text-cf-muted">
            Run
            <select
              className="ml-1 max-w-[11rem] rounded border border-cf-border bg-cf-surface-2 px-1 py-0.5 font-mono text-cf-ink"
              value={runId}
              onChange={(e) => setRunId(e.target.value)}
            >
              <option value="">Any</option>
              {runIds.map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
          </label>
          <label className="text-cf-muted">
            App
            <select
              className="ml-1 rounded border border-cf-border bg-cf-surface-2 px-1 py-0.5 text-cf-ink"
              value={appVersion}
              onChange={(e) => setAppVersion(e.target.value)}
            >
              <option value="">Any</option>
              {versions.app.map((v) => (
                <option key={v} value={v}>
                  v{v}
                </option>
              ))}
            </select>
          </label>
          <label className="text-cf-muted">
            Modules
            <select
              className="ml-1 rounded border border-cf-border bg-cf-surface-2 px-1 py-0.5 text-cf-ink"
              value={modulesVersion}
              onChange={(e) => setModulesVersion(e.target.value)}
            >
              <option value="">Any</option>
              {versions.modules.map((v) => (
                <option key={v} value={v}>
                  v{v}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>
      <div className="border-b border-cf-border px-3 py-1 text-[10px] text-cf-muted">
        Showing {entries.length} of {contextual.length} lines
        {filteredCount < contextual.length ? ` (${filteredCount} after filters)` : ""}
        {tailTruncated ? ` · tail capped at ${TAIL_LINES}` : ""}
      </div>
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="min-w-0 flex-1 overflow-auto p-2 font-mono text-[11px] leading-relaxed"
      >
        {entries.map((e, i) => (
          <div key={i} className={`${eventLogLineClass(e.raw)} overflow-x-auto whitespace-pre`}>
            {e.raw}
          </div>
        ))}
        {entries.length === 0 && <div className="p-3 text-cf-muted">No log entries.</div>}
      </div>
    </div>
  );
}
