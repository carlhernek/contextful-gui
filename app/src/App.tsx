import { useEffect, useState } from "react";
import { api, type Project, type SetupStatus } from "./lib/ipc";
import { SetupWizard } from "./components/SetupWizard";
import { ProjectSidebar } from "./components/ProjectSidebar";
import { PipelineTab } from "./components/PipelineTab";
import { ChatPanel } from "./components/ChatPanel";
import { MetaDocumentsTab } from "./components/MetaDocumentsTab";
import { RepositoriesTab } from "./components/RepositoriesTab";
import { RunHistory } from "./components/RunHistory";
import { ResultsView } from "./components/ResultsView";
import { EventLogPanel } from "./components/EventLogPanel";
import { SettingsModal } from "./components/SettingsModal";
import { ActivityIndicator } from "./components/ActivityIndicator";
import { Spinner } from "./components/Spinner";
import { JobsProvider } from "./lib/jobs";

type Tab = "chat" | "pipeline" | "meta" | "repos" | "results" | "logs";

const TAB_LABELS: Record<Tab, string> = {
  chat: "Chat",
  pipeline: "Pipeline",
  meta: "Meta documents",
  repos: "Repositories",
  results: "Results",
  logs: "Logs",
};

function WorkspaceView({ status, onReset }: { status: SetupStatus; onReset: () => void }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeId, setActiveId] = useState<string | null>(status.activeProject);
  const [selected, setSelected] = useState<string[]>([]);
  const [tab, setTab] = useState<Tab>("pipeline");
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [historyKey, setHistoryKey] = useState(0);
  const [modulesRefreshKey, setModulesRefreshKey] = useState(0);
  const [showSettings, setShowSettings] = useState(false);

  const refreshProjects = async () => setProjects(await api.listProjects());

  useEffect(() => {
    void (async () => {
      const list = await api.listProjects();
      setProjects(list);
      if (!activeId) {
        const pid = status.activeProject ?? list[0]?.id ?? null;
        if (pid) {
          setActiveId(pid);
          await api.setActiveProject(pid);
          await api.getModulesVersionStatus(pid, true);
        }
      } else {
        await api.getModulesVersionStatus(activeId, true);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectProject = async (id: string) => {
    setActiveId(id);
    setActiveRunId(null);
    setSelected([]);
    await api.setActiveProject(id);
  };

  const active = projects.find((p) => p.id === activeId) ?? null;

  const handleRunIntent = (modules: string[], _force: boolean) => {
    setSelected(modules);
    setTab("pipeline");
  };

  return (
    <div className="flex h-screen">
      <ProjectSidebar
        projects={projects}
        activeId={activeId}
        onSelect={selectProject}
        onChanged={refreshProjects}
      />

      <main className="flex flex-1 flex-col overflow-hidden">
        <header className="flex items-center justify-between border-b border-cf-border px-5 py-3">
          <h1 className="text-lg font-semibold text-cf-ink">
            {active?.display_name ?? "Contextful"}
          </h1>
          <div className="flex items-center gap-3">
            <ActivityIndicator />
            {(["chat", "pipeline", "meta", "repos", "results", "logs"] as Tab[]).map((t) => (
              <button
                key={t}
                type="button"
                className={`text-sm ${
                  tab === t
                    ? "border-b-2 border-cf-accent font-medium text-cf-ink"
                    : "text-cf-muted hover:text-cf-ink"
                }`}
                onClick={() => setTab(t)}
              >
                {TAB_LABELS[t]}
              </button>
            ))}
            {active && (
              <button
                type="button"
                className="rounded-md border border-cf-border px-2 py-1 text-xs text-cf-ink hover:bg-cf-surface-2"
                onClick={() => setShowSettings(true)}
              >
                Settings
              </button>
            )}
          </div>
        </header>

        <div className="flex-1 overflow-auto p-5">
          {!active ? (
            <p className="text-sm text-cf-muted">
              Create or select a project from the sidebar to begin.
            </p>
          ) : tab === "chat" ? (
            <ChatPanel projectId={active.id} onRunIntent={handleRunIntent} />
          ) : tab === "pipeline" ? (
            <PipelineTab
              projectId={active.id}
              selected={selected}
              onChangeSelected={setSelected}
              modulesRefreshKey={modulesRefreshKey}
              onRunStart={(runId) => {
                setActiveRunId(runId);
                setHistoryKey((k) => k + 1);
              }}
              onComplete={(runId) => {
                setActiveRunId(runId);
                setHistoryKey((k) => k + 1);
                setTab("results");
              }}
            />
          ) : tab === "meta" ? (
            <MetaDocumentsTab projectId={active.id} />
          ) : tab === "repos" ? (
            <RepositoriesTab projectId={active.id} />
          ) : tab === "results" ? (
            <div className="mx-auto grid max-w-5xl grid-cols-[260px_minmax(0,1fr)] gap-4">
              <RunHistory
                projectId={active.id}
                activeRunId={activeRunId}
                refreshKey={historyKey}
                onSelect={setActiveRunId}
              />
              <div className="h-[70vh] min-w-0 overflow-hidden rounded-lg border border-cf-border bg-cf-surface">
                <ResultsView projectId={active.id} runId={activeRunId} />
              </div>
            </div>
          ) : (
            <div className="mx-auto h-[75vh] min-w-0 max-w-4xl overflow-hidden">
              <EventLogPanel projectId={active.id} />
            </div>
          )}
        </div>
      </main>

      {showSettings && active && (
        <SettingsModal
          projectId={active.id}
          projectType={active.project_type}
          onClose={() => setShowSettings(false)}
          onSaved={refreshProjects}
          onModulesUpdated={() => setModulesRefreshKey((k) => k + 1)}
          onRerunSetup={onReset}
        />
      )}
    </div>
  );
}

export default function App() {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [forceSetup, setForceSetup] = useState(false);

  const refresh = async () => setStatus(await api.getSetupStatus());

  useEffect(() => {
    void refresh();
  }, []);

  if (!status) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner size={24} />
      </div>
    );
  }

  const ready = status.hasApiKey && status.templateReady && !!status.installPath;

  if (!ready || forceSetup) {
    return (
      <SetupWizard
        onReady={async () => {
          await refresh();
          setForceSetup(false);
        }}
      />
    );
  }

  return (
    <JobsProvider>
      <WorkspaceView status={status} onReset={() => setForceSetup(true)} />
    </JobsProvider>
  );
}
