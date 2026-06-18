import { useEffect, useRef, useState } from "react";
import { api } from "../lib/ipc";
import { parseEventLog, filterEntries, type LogFilter } from "../lib/eventLog";
import { eventLogLineClass } from "../lib/statusStyles";

const FILTERS: LogFilter[] = ["ALL", "Ops", "TURN", "ERROR", "TOOL"];
const REFRESH_MS = 10_000;
const STICKY_THRESHOLD_PX = 48;

export function EventLogPanel({ projectId }: { projectId: string }) {
  const [text, setText] = useState("");
  const [filter, setFilter] = useState<LogFilter>("ALL");
  const scrollRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  const refresh = async () => setText(await api.getEventLog(projectId));

  useEffect(() => {
    void refresh();
    const t = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const entries = filterEntries(parseEventLog(text), filter);

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

  return (
    <div className="flex h-full flex-col rounded-lg border border-cf-border bg-cf-surface">
      <div className="flex items-center justify-between border-b border-cf-border px-3 py-2">
        <div className="flex gap-1">
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
        <button className="text-xs text-cf-muted hover:text-cf-ink" onClick={copyFiltered}>
          copy
        </button>
      </div>
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="flex-1 overflow-auto p-2 font-mono text-[11px] leading-relaxed"
      >
        {entries.map((e, i) => (
          <div key={i} className={eventLogLineClass(e.raw)}>
            {e.raw}
          </div>
        ))}
        {entries.length === 0 && <div className="p-3 text-cf-muted">No log entries.</div>}
      </div>
    </div>
  );
}
