import { useState } from "react";
import type { TasksDoc } from "../lib/ipc";
import {
  downloadText,
  tasksFilename,
  tasksToJson,
  tasksToMarkdown,
} from "../lib/taskExport";
import { KanbanBoard } from "./KanbanBoard";

interface Props {
  tasksDoc: TasksDoc | null;
  moduleId: string | null;
}

export function TasksPanel({ tasksDoc, moduleId }: Props) {
  const [copied, setCopied] = useState<string | null>(null);

  const tasks = tasksDoc?.tasks ?? [];
  const doc = tasksDoc ?? null;

  const flash = (label: string) => {
    setCopied(label);
    window.setTimeout(() => setCopied(null), 1500);
  };

  const copyMd = () => {
    if (!doc) return;
    void navigator.clipboard.writeText(tasksToMarkdown(doc));
    flash("md");
  };

  const copyJson = () => {
    if (!doc) return;
    void navigator.clipboard.writeText(tasksToJson(doc));
    flash("json");
  };

  const downloadMd = () => {
    if (!doc || !moduleId) return;
    downloadText(tasksFilename(moduleId, "md"), tasksToMarkdown(doc), "text/markdown");
  };

  const downloadJson = () => {
    if (!doc || !moduleId) return;
    downloadText(tasksFilename(moduleId, "json"), tasksToJson(doc), "application/json");
  };

  const disabled = !doc || tasks.length === 0;

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm text-cf-muted">
          {tasks.length ? `${tasks.length} tasks` : "No tasks"}
        </span>
        <div className="flex flex-wrap gap-1">
          <button
            type="button"
            className="rounded border border-cf-border px-2 py-0.5 text-xs text-cf-ink hover:bg-cf-surface-2 disabled:opacity-40"
            disabled={disabled}
            onClick={copyMd}
          >
            {copied === "md" ? "Copied" : "Copy MD"}
          </button>
          <button
            type="button"
            className="rounded border border-cf-border px-2 py-0.5 text-xs text-cf-ink hover:bg-cf-surface-2 disabled:opacity-40"
            disabled={disabled}
            onClick={downloadMd}
          >
            Download MD
          </button>
          <button
            type="button"
            className="rounded border border-cf-border px-2 py-0.5 text-xs text-cf-ink hover:bg-cf-surface-2 disabled:opacity-40"
            disabled={disabled}
            onClick={copyJson}
          >
            {copied === "json" ? "Copied" : "Copy JSON"}
          </button>
          <button
            type="button"
            className="rounded border border-cf-border px-2 py-0.5 text-xs text-cf-ink hover:bg-cf-surface-2 disabled:opacity-40"
            disabled={disabled}
            onClick={downloadJson}
          >
            Download JSON
          </button>
        </div>
      </div>
      <KanbanBoard tasks={tasks} />
    </div>
  );
}
