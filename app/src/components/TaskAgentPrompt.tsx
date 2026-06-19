import { useState } from "react";
import type { Task } from "../lib/ipc";
import { formatTaskPrompt } from "../lib/taskPrompt";

interface Props {
  task: Task;
  moduleId?: string | null;
}

export function TaskAgentPrompt({ task, moduleId }: Props) {
  const [copied, setCopied] = useState(false);
  const prompt = formatTaskPrompt(task, moduleId ?? undefined);

  const copy = () => {
    void navigator.clipboard.writeText(prompt).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };

  if (!task.agentic_spec?.trim() && task.evidence.length === 0) {
    return null;
  }

  return (
    <details className="mt-2">
      <summary className="cursor-pointer text-xs text-cf-muted">agent prompt</summary>
      <div className="mt-1 rounded border border-cf-border bg-cf-surface p-2">
        <div className="mb-1 flex justify-end">
          <button
            type="button"
            className="rounded border border-cf-border px-2 py-0.5 text-[10px] text-cf-ink hover:bg-cf-surface-2"
            onClick={(e) => {
              e.preventDefault();
              copy();
            }}
          >
            {copied ? "Copied" : "Copy prompt"}
          </button>
        </div>
        <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-cf-ink">
          {prompt}
        </pre>
      </div>
    </details>
  );
}
