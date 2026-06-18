import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { api, type RunArtifacts } from "../lib/ipc";
import { KanbanBoard } from "./KanbanBoard";
import { IndexButton } from "./IndexButton";
import { Spinner } from "./Spinner";

export function ResultsView({ projectId, runId }: { projectId: string; runId: string | null }) {
  const [artifacts, setArtifacts] = useState<RunArtifacts | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<string>("");
  const [tab, setTab] = useState<"analysis" | "tasks">("analysis");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!runId) {
      setArtifacts(null);
      return;
    }
    (async () => {
      setLoading(true);
      try {
        const a = await api.getRunArtifacts(projectId, runId);
        setArtifacts(a);
        setActive(a.modules[0]?.moduleId ?? null);
      } finally {
        setLoading(false);
      }
    })();
  }, [projectId, runId]);

  useEffect(() => {
    if (!runId || !active) return;
    (async () => {
      const res = await api.previewFile(projectId, `${runId}/${active}/analysis.md`, "runs");
      setAnalysis(res.ok ? res.content ?? "" : `_${res.error}_`);
    })();
  }, [projectId, runId, active]);

  if (!runId) {
    return <p className="p-6 text-sm text-cf-muted">Select a run to view results.</p>;
  }
  if (loading) {
    return (
      <div className="p-6">
        <Spinner />
      </div>
    );
  }

  const current = artifacts?.modules.find((m) => m.moduleId === active);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1 overflow-x-auto border-b border-cf-border px-3 py-2">
        {artifacts?.modules.map((m) => (
          <button
            key={m.moduleId}
            className={`whitespace-nowrap rounded-md px-3 py-1 text-sm ${
              m.moduleId === active
                ? "bg-cf-surface-2 text-cf-ink"
                : "text-cf-muted hover:text-cf-ink"
            }`}
            onClick={() => setActive(m.moduleId)}
          >
            {m.moduleId}
          </button>
        ))}
      </div>

      <div className="flex gap-1 px-3 pt-3">
        <button
          className={`rounded-md px-3 py-1 text-sm ${tab === "analysis" ? "bg-cf-accent text-cf-accent-ink" : "text-cf-muted hover:text-cf-ink"}`}
          onClick={() => setTab("analysis")}
        >
          Analysis
        </button>
        {runId && active && (
          <IndexButton
            projectId={projectId}
            itemId={`artifact:${runId}/${active}/analysis.md`}
            title="Index analysis artefact"
          />
        )}
        <button
          className={`rounded-md px-3 py-1 text-sm ${tab === "tasks" ? "bg-cf-accent text-cf-accent-ink" : "text-cf-muted hover:text-cf-ink"}`}
          onClick={() => setTab("tasks")}
        >
          Tasks ({current?.tasks?.tasks.length ?? 0})
        </button>
        {runId && active && current?.tasks && (
          <IndexButton
            projectId={projectId}
            itemId={`artifact:${runId}/${active}/tasks.json`}
            title="Index tasks artefact"
          />
        )}
      </div>

      <div className="flex-1 overflow-auto p-4">
        {tab === "analysis" ? (
          <div className="cf-markdown max-w-3xl">
            <Markdown remarkPlugins={[remarkGfm, remarkBreaks]}>{analysis}</Markdown>
          </div>
        ) : (
          <KanbanBoard tasks={current?.tasks?.tasks ?? []} />
        )}
      </div>
    </div>
  );
}
