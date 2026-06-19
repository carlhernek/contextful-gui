import { useCallback, useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { api, onContextfulEvent, type RunArtifacts } from "../lib/ipc";
import { useRunProgress } from "../hooks/useRunProgress";
import { TasksPanel } from "./TasksPanel";
import { IndexButton } from "./IndexButton";
import { RunModuleProgress } from "./RunModuleProgress";
import { Spinner } from "./Spinner";
import { ActivityFeed } from "./ActivityFeed";
import { useJob } from "../lib/jobs";

const WORKSPACE_INDEX = "workspace-index";

function finishedModules(artifacts: RunArtifacts | null): string[] {
  return (
    artifacts?.modules
      .filter((m) => m.hasAnalysis || m.tasks)
      .map((m) => m.moduleId) ?? []
  );
}

export function ResultsView({ projectId, runId }: { projectId: string; runId: string | null }) {
  const [artifacts, setArtifacts] = useState<RunArtifacts | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [tab, setTab] = useState<"analysis" | "tasks" | "activity">("analysis");
  const [loading, setLoading] = useState(false);
  const { busy: runBusy } = useJob("run", projectId);
  const artifactModuleIds = artifacts?.modules.map((m) => m.moduleId) ?? [];
  const finishedModuleIds = finishedModules(artifacts);
  const { stages } = useRunProgress(
    projectId,
    runId,
    artifactModuleIds,
    [],
    finishedModuleIds,
  );

  const loadArtifacts = useCallback(
    async (id: string, quiet = false) => {
      if (!quiet) setLoading(true);
      try {
        const a = await api.getRunArtifacts(projectId, id);
        setArtifacts(a);
        setActive((prev) => {
          if (prev && a.modules.some((m) => m.moduleId === prev)) return prev;
          return a.modules[0]?.moduleId ?? null;
        });
      } finally {
        if (!quiet) setLoading(false);
      }
    },
    [projectId],
  );

  useEffect(() => {
    if (!runId) {
      setArtifacts(null);
      return;
    }
    void loadArtifacts(runId);
  }, [projectId, runId, loadArtifacts]);

  useEffect(() => {
    if (!runId) return;
    let unlisten: (() => void) | undefined;
    onContextfulEvent((e) => {
      if (e.event === "module") {
        const d = e.data as { status?: string };
        if (d.status === "SUCCESS" || d.status === "ERROR") {
          void loadArtifacts(runId, true);
        }
      } else if (e.event === "run") {
        const d = e.data as { status?: string };
        if (d.status && d.status !== "running") {
          void loadArtifacts(runId, true);
        }
      } else if (e.event === "index") {
        void loadArtifacts(runId, true);
      }
    }).then((fn) => (unlisten = fn));
    return () => unlisten?.();
  }, [projectId, runId, loadArtifacts]);

  useEffect(() => {
    if (active === WORKSPACE_INDEX && runBusy) {
      setTab("activity");
    }
  }, [active, runBusy]);

  if (!runId) {
    return <p className="p-6 text-sm text-cf-muted">Select a run to view results.</p>;
  }
  if (loading && !artifacts) {
    return (
      <div className="p-6">
        <Spinner />
      </div>
    );
  }

  const current = artifacts?.modules.find((m) => m.moduleId === active);
  const analysis = current?.analysis ?? "";
  const showActivity = tab === "activity" && active;

  const indexItem =
    active !== WORKSPACE_INDEX && runId && active
      ? tab === "analysis" && current?.hasAnalysis
        ? { id: `artifact:${runId}/${active}/analysis.md`, title: "Index analysis artefact" }
        : tab === "tasks" && current?.tasks
          ? { id: `artifact:${runId}/${active}/tasks.json`, title: "Index tasks artefact" }
          : null
      : null;

  return (
    <div className="flex h-full min-w-0 flex-col overflow-hidden">
      {stages.length > 1 && (
        <div className="border-b border-cf-border px-3 py-2">
          <RunModuleProgress stages={stages} compact />
        </div>
      )}
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

      <div className="flex w-full items-center gap-1 px-3 pt-3">
        <div className="flex gap-1">
          <button
            className={`rounded-md px-3 py-1 text-sm ${tab === "analysis" ? "bg-cf-accent text-cf-accent-ink" : "text-cf-muted hover:text-cf-ink"}`}
            onClick={() => setTab("analysis")}
          >
            Analysis
          </button>
          <button
            className={`rounded-md px-3 py-1 text-sm ${tab === "tasks" ? "bg-cf-accent text-cf-accent-ink" : "text-cf-muted hover:text-cf-ink"}`}
            onClick={() => setTab("tasks")}
          >
            Tasks ({current?.tasks?.tasks.length ?? 0})
          </button>
          <button
            className={`rounded-md px-3 py-1 text-sm ${tab === "activity" ? "bg-cf-accent text-cf-accent-ink" : "text-cf-muted hover:text-cf-ink"}`}
            onClick={() => setTab("activity")}
          >
            Activity
          </button>
        </div>
        {indexItem && (
          <div className="ml-auto flex shrink-0 gap-1">
            <IndexButton
              projectId={projectId}
              itemId={indexItem.id}
              title={indexItem.title}
            />
          </div>
        )}
      </div>

      <div className="min-w-0 flex-1 overflow-auto p-4">
        {tab === "analysis" ? (
          loading && !analysis ? (
            <Spinner />
          ) : analysis ? (
            <div className="cf-markdown max-w-3xl">
              <Markdown remarkPlugins={[remarkGfm, remarkBreaks]}>{analysis}</Markdown>
            </div>
          ) : (
            <p className="text-sm text-cf-muted">No analysis available for this module.</p>
          )
        ) : tab === "tasks" ? (
          <TasksPanel tasksDoc={current?.tasks ?? null} moduleId={active} />
        ) : showActivity ? (
          <ActivityFeed
            projectId={projectId}
            runId={runId}
            moduleId={active}
            live={runBusy}
          />
        ) : (
          <p className="text-sm text-cf-muted">Select a module to view activity.</p>
        )}
      </div>
    </div>
  );
}
