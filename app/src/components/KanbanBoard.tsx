import type { Task } from "../lib/ipc";
import { priorityClass } from "../lib/statusStyles";

const COLUMNS: { key: Task["priority"]; label: string }[] = [
  { key: "high", label: "High" },
  { key: "medium", label: "Medium" },
  { key: "low", label: "Low" },
];

export function KanbanBoard({ tasks }: { tasks: Task[] }) {
  if (!tasks.length) {
    return <p className="text-sm text-cf-muted">No tasks produced for this module.</p>;
  }
  return (
    <div className="grid grid-cols-3 gap-3">
      {COLUMNS.map((col) => {
        const items = tasks.filter((t) => t.priority === col.key);
        return (
          <div key={col.key} className="flex flex-col gap-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-cf-muted">
              {col.label} ({items.length})
            </div>
            {items.map((t) => (
              <article
                key={t.id}
                className="rounded-md border border-cf-border bg-cf-surface-2 p-3"
              >
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="font-mono text-xs text-cf-muted">{t.id}</span>
                  <span
                    className={`rounded-full border px-1.5 py-0.5 text-[10px] ${priorityClass[t.priority]}`}
                  >
                    {t.priority} · {t.effort}
                  </span>
                </div>
                <h4 className="text-sm font-medium text-cf-ink">{t.title}</h4>
                <p className="mt-1 text-xs text-cf-muted">{t.rationale}</p>
                {t.evidence.length > 0 && (
                  <ul className="mt-2 space-y-0.5">
                    {t.evidence.map((e, i) => (
                      <li key={i} className="font-mono text-[11px] text-cf-info">
                        {e}
                      </li>
                    ))}
                  </ul>
                )}
                {t.agentic_spec && (
                  <details className="mt-2">
                    <summary className="cursor-pointer text-xs text-cf-muted">agentic spec</summary>
                    <p className="mt-1 whitespace-pre-wrap text-xs text-cf-ink">{t.agentic_spec}</p>
                  </details>
                )}
              </article>
            ))}
          </div>
        );
      })}
    </div>
  );
}
