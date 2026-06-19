import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import {
  api,
  onContextfulEvent,
  type ActivityEntry,
  type SidecarEvent,
} from "../lib/ipc";
import { Spinner } from "./Spinner";

interface Props {
  projectId: string;
  runId: string;
  moduleId: string;
  live?: boolean;
}

const MAX_VISIBLE_ENTRIES = 1000;

function entryKey(e: ActivityEntry, idx: number): string {
  return `${e.seq ?? idx}-${e.kind}-${e.turn ?? ""}-${e.itemId ?? ""}-${e.name ?? ""}`;
}

function groupByItem(entries: ActivityEntry[]): Map<string, ActivityEntry[]> {
  const groups = new Map<string, ActivityEntry[]>();
  for (const e of entries) {
    const key = e.itemId ?? "__general__";
    const list = groups.get(key) ?? [];
    list.push(e);
    groups.set(key, list);
  }
  return groups;
}

function itemLabel(entry: ActivityEntry): string {
  if (entry.path) return entry.path;
  if (entry.itemId) return entry.itemId;
  return "item";
}

const STEP_KINDS = new Set([
  "scan_start",
  "scan_done",
  "scan_item",
  "index_start",
  "index_done",
  "cache_hit",
  "cache_miss",
  "llm_request",
  "llm_response",
]);

function itemStatus(entries: ActivityEntry[]): string {
  const last = [...entries].reverse().find((e) => e.kind === "item");
  if (last?.status) return last.status;
  if (entries.some((e) => e.kind === "final" || e.kind === "error")) return "done";
  if (entries.some((e) => e.kind === "index_done" || e.kind === "cache_hit")) return "done";
  if (
    entries.some((e) =>
      e.kind === "turn" ||
      e.kind === "thinking" ||
      e.kind === "tool" ||
      e.kind === "index_start" ||
      e.kind === "llm_request" ||
      e.kind === "llm_response",
    )
  ) {
    return "indexing";
  }
  if (entries.some((e) => e.kind === "scan_start") && !entries.some((e) => e.kind === "scan_done")) {
    return "scanning";
  }
  if (entries.some((e) => e.kind === "scan_item")) return "scanning";
  return "pending";
}

function stepLabel(entry: ActivityEntry): string {
  if (entry.text?.trim()) return entry.text.trim();
  const bits: string[] = [];
  if (entry.path) bits.push(entry.path);
  if (entry.durationMs != null) bits.push(`${entry.durationMs}ms`);
  if (entry.itemIndex != null && entry.itemTotal != null) {
    bits.push(`(${entry.itemIndex}/${entry.itemTotal})`);
  }
  if (entry.itemCount != null) bits.push(`${entry.itemCount} items`);
  return bits.join(" ") || entry.kind.replace(/_/g, " ");
}

function FeedEntry({ entry }: { entry: ActivityEntry }) {
  if (STEP_KINDS.has(entry.kind)) {
    return (
      <div className="font-mono text-xs text-cf-muted">
        <span className="uppercase tracking-wide text-cf-info/80">{entry.kind.replace(/_/g, " ")}</span>
        <span className="ml-2">{stepLabel(entry)}</span>
      </div>
    );
  }

  switch (entry.kind) {
    case "turn":
      return (
        <div className="text-xs font-medium uppercase tracking-wide text-cf-muted">
          Turn {entry.turn}/{entry.maxTurns}
        </div>
      );
    case "thinking":
    case "final":
      return (
        <div className="cf-markdown rounded-md border border-cf-border/60 bg-cf-surface-2/40 px-3 py-2 text-sm">
          <Markdown remarkPlugins={[remarkGfm, remarkBreaks]}>{entry.text ?? ""}</Markdown>
        </div>
      );
    case "tool":
      return (
        <div className="rounded-md border border-cf-info/30 bg-cf-surface-2/30 px-3 py-2 text-sm">
          <span className="font-mono text-cf-info">{entry.name}</span>
          {entry.args && (
            <pre className="mt-1 overflow-x-auto text-xs text-cf-muted">
              {JSON.stringify(entry.args, null, 2)}
            </pre>
          )}
        </div>
      );
    case "tool_result":
      return (
        <details className="rounded-md border border-cf-border/50 px-3 py-2 text-sm">
          <summary className="cursor-pointer font-mono text-xs text-cf-muted">
            {entry.name} result
          </summary>
          <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-xs text-cf-ink">
            {entry.result ?? ""}
          </pre>
        </details>
      );
    case "item":
      return (
        <div className="text-sm text-cf-muted">
          Item {entry.status}: {itemLabel(entry)}
          {entry.description ? ` — ${entry.description}` : ""}
        </div>
      );
    case "error":
      return <div className="text-sm text-cf-danger">{entry.text}</div>;
    default:
      return null;
  }
}

export function ActivityFeed({ projectId, runId, moduleId, live = true }: Props) {
  const [entries, setEntries] = useState<ActivityEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [streamingText, setStreamingText] = useState("");
  const [showAll, setShowAll] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const liveTurnRef = useRef<number | null>(null);

  const reload = useCallback(async () => {
    try {
      const data = await api.getRunActivity(projectId, runId, moduleId);
      setEntries(data.entries ?? []);
      setStreamingText("");
    } catch {
      setEntries([]);
    }
  }, [projectId, runId, moduleId]);

  useEffect(() => {
    setLoading(true);
    void reload().finally(() => setLoading(false));
  }, [reload]);

  useEffect(() => {
    if (!live) return;
    let unlisten: (() => void) | undefined;

    const appendLive = (entry: ActivityEntry) => {
      setEntries((prev) => [...prev, entry]);
    };

    const handle = (e: SidecarEvent) => {
      if (e.event === "module") {
        const d = e.data as { module: string; status: string };
        if (d.module === moduleId && (d.status === "SUCCESS" || d.status === "ERROR")) {
          void reload();
        }
        return;
      }
      if (e.event === "run" || e.event === "activity" || e.event === "index") {
        void reload();
        return;
      }

      const d = (e.data ?? {}) as Record<string, unknown>;
      const evModule = d.module as string | undefined;
      if (evModule && evModule !== moduleId) return;

      if (e.event === "turn") {
        liveTurnRef.current = d.turn as number;
        setStreamingText("");
        appendLive({
          seq: Date.now(),
          ts: new Date().toISOString(),
          kind: "turn",
          turn: d.turn as number,
          maxTurns: d.maxTurns as number,
          itemId: d.itemId as string | undefined,
          itemIndex: d.itemIndex as number | undefined,
          itemTotal: d.itemTotal as number | undefined,
        });
      } else if (e.event === "token" && typeof e.data === "string") {
        setStreamingText((s) => s + e.data);
      } else if (e.event === "tool") {
        appendLive({
          seq: Date.now(),
          ts: new Date().toISOString(),
          kind: "tool",
          turn: liveTurnRef.current ?? undefined,
          name: d.name as string,
          args: d.args as Record<string, unknown>,
          itemId: d.itemId as string | undefined,
          itemIndex: d.itemIndex as number | undefined,
          itemTotal: d.itemTotal as number | undefined,
        });
      } else if (e.event === "index" && moduleId === "workspace-index") {
        appendLive({
          seq: Date.now(),
          ts: new Date().toISOString(),
          kind: "item",
          status: d.status as string,
          itemId: d.itemId as string | undefined,
          itemIndex: d.index as number | undefined,
          itemTotal: d.total as number | undefined,
        });
      }
    };

    onContextfulEvent(handle).then((fn) => (unlisten = fn));
    return () => unlisten?.();
  }, [live, moduleId, reload]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [entries, streamingText]);

  if (loading) {
    return (
      <div className="p-4">
        <Spinner />
      </div>
    );
  }

  const hasStreaming = streamingText.length > 0;
  const truncated = !showAll && entries.length > MAX_VISIBLE_ENTRIES;
  const visibleEntries = truncated ? entries.slice(-MAX_VISIBLE_ENTRIES) : entries;
  const isIndexModule = moduleId === "workspace-index";
  const hasItemGroups = isIndexModule && visibleEntries.some((e) => e.itemId);
  const groups = hasItemGroups ? groupByItem(visibleEntries) : null;
  const flatSteps = isIndexModule && !hasItemGroups ? visibleEntries : null;

  if (entries.length === 0 && !hasStreaming) {
    return (
      <p className="p-4 text-sm text-cf-muted">
        No agent activity recorded for this module yet.
      </p>
    );
  }

  return (
    <div className="space-y-4 pb-4">
      {truncated && (
        <button
          type="button"
          className="text-xs text-cf-info hover:underline"
          onClick={() => setShowAll(true)}
        >
          Show all {entries.length} entries (showing last {MAX_VISIBLE_ENTRIES})
        </button>
      )}
      {flatSteps ? (
        <div className="space-y-1">
          {flatSteps.map((entry, i) => (
            <FeedEntry key={entryKey(entry, i)} entry={entry} />
          ))}
        </div>
      ) : groups ? (
        [...groups.entries()].map(([itemId, itemEntries]) => {
          const first = itemEntries[0];
          const status = itemStatus(itemEntries);
          const idx = first?.itemIndex;
          const total = first?.itemTotal;
          const title =
            itemId === "__general__"
              ? "General"
              : `${itemLabel(first ?? { itemId })}${idx && total ? ` (${idx}/${total})` : ""}`;
          return (
            <section key={itemId} className="rounded-lg border border-cf-border p-3">
              <div className="mb-2 flex items-center justify-between gap-2">
                <h4 className="font-mono text-xs text-cf-ink">{title}</h4>
                <span className="text-xs capitalize text-cf-muted">{status}</span>
              </div>
              <div className="space-y-2">
                {itemEntries.map((entry, i) => (
                  <FeedEntry key={entryKey(entry, i)} entry={entry} />
                ))}
              </div>
            </section>
          );
        })
      ) : (
        <div className="space-y-2">
          {visibleEntries.map((entry, i) => (
            <FeedEntry key={entryKey(entry, i)} entry={entry} />
          ))}
        </div>
      )}
      {hasStreaming && (
        <div className="cf-markdown rounded-md border border-dashed border-cf-info/40 bg-cf-surface-2/20 px-3 py-2 text-sm">
          <Markdown remarkPlugins={[remarkGfm, remarkBreaks]}>{streamingText}</Markdown>
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
