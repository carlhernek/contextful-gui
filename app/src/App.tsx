import { useEffect, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { api, type Project, type SetupStatus } from "./lib/ipc";
import { SetupWizard } from "./components/SetupWizard";
import { ProjectSidebar } from "./components/ProjectSidebar";
import { RepoManager } from "./components/RepoManager";
import { ModuleSelector } from "./components/ModuleSelector";
import { RunPanel } from "./components/RunPanel";
import { RunHistory } from "./components/RunHistory";
import { ResultsView } from "./components/ResultsView";
import { EventLogPanel } from "./components/EventLogPanel";
import { SettingsModal } from "./components/SettingsModal";
import { ModulesVersionBadge } from "./components/ModulesVersionBadge";
import { Spinner } from "./components/Spinner";

type Tab = "configure" | "results" | "logs";

function MetaFiles({ projectId }: { projectId: string }) {
  const [files, setFiles] = useState<{ name: string; size: number }[]>([]);

  const refresh = async () => setFiles(await api.listMetaFiles(projectId));
  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const add = async () => {
    const picked = await open({ multiple: true });
    if (!picked) return;
    const paths = Array.isArray(picked) ? picked : [picked];
    await api.uploadMetaFiles(projectId, paths);
    await refresh();
  };

  return (
    <div className="rounded-lg border border-cf-border bg-cf-surface p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-semibold text-cf-ink">Meta documents</h3>
        <button
          className="rounded-md border border-cf-border px-2 py-1 text-xs text-cf-ink hover:bg-cf-surface-2"
          onClick={add}
        >
          Upload…
        </button>
      </div>
      {files.length === 0 ? (
        <p className="text-sm text-cf-muted">No meta documents uploaded.</p>
      ) : (
        <div className="space-y-1">
          {files.map((f) => (
            <div
              key={f.name}
              className="flex items-center justify-between rounded-md bg-cf-surface-2 px-3 py-1.5 text-sm"
            >
              <span className="text-cf-ink">{f.name}</span>
              <button
                className="text-cf-danger"
                onClick={async () => {
                  await api.deleteMetaFile(projectId, f.name);
                  await refresh();
                }}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ChatBox({ projectId }: { projectId: string }) {
  const [message, setMessage] = useState("");
  const [reply, setReply] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const send = async () => {
    if (!message.trim()) return;
    setBusy(true);
    setReply(null);
    try {
      const res = await api.sendChat(projectId, message.trim());
      setReply(res.reply);
      setMessage("");
    } catch (e) {
      setReply(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-lg border border-cf-border bg-cf-surface p-4">
      <h3 className="mb-3 font-semibold text-cf-ink">Ask the orchestrator</h3>
      <div className="flex gap-2">
        <input
          className="flex-1 rounded-md border border-cf-border bg-cf-surface-2 px-2 py-1.5 text-sm text-cf-ink outline-none focus:border-cf-accent"
          placeholder="e.g. what did the security module find?"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
        />
        <button
          className="rounded-md bg-cf-accent px-3 py-1.5 text-sm font-medium text-cf-accent-ink hover:opacity-90"
          onClick={send}
          disabled={busy}
        >
          {busy ? <Spinner size={12} /> : "Send"}
        </button>
      </div>
      {reply && <p className="mt-3 whitespace-pre-wrap text-sm text-cf-ink">{reply}</p>}
    </div>
  );
}

function WorkspaceView({ status, onReset }: { status: SetupStatus; onReset: () => void }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeId, setActiveId] = useState<string | null>(status.activeProject);
  const [selected, setSelected] = useState<string[]>([]);
  const [tab, setTab] = useState<Tab>("configure");
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [historyKey, setHistoryKey] = useState(0);
  const [showSettings, setShowSettings] = useState(false);

  const refreshProjects = async () => setProjects(await api.listProjects());

  useEffect(() => {
    void refreshProjects();
  }, []);

  const selectProject = async (id: string) => {
    setActiveId(id);
    setActiveRunId(null);
    setSelected([]);
    await api.setActiveProject(id);
  };

  const active = projects.find((p) => p.id === activeId) ?? null;

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
          <div>
            <h1 className="text-lg font-semibold text-cf-ink">
              {active?.display_name ?? "Contextful"}
            </h1>
            {active && <ModulesVersionBadge projectId={active.id} />}
          </div>
          <div className="flex items-center gap-3">
            {(["configure", "results", "logs"] as Tab[]).map((t) => (
              <button
                key={t}
                className={`text-sm capitalize ${
                  tab === t ? "text-cf-ink" : "text-cf-muted hover:text-cf-ink"
                }`}
                onClick={() => setTab(t)}
              >
                {t}
              </button>
            ))}
            {active && (
              <button
                className="rounded-md border border-cf-border px-2 py-1 text-xs text-cf-ink hover:bg-cf-surface-2"
                onClick={() => setShowSettings(true)}
              >
                Settings
              </button>
            )}
            <button
              className="text-xs text-cf-muted hover:text-cf-ink"
              onClick={onReset}
              title="Re-run setup"
            >
              setup
            </button>
          </div>
        </header>

        <div className="flex-1 overflow-auto p-5">
          {!active ? (
            <p className="text-sm text-cf-muted">
              Create or select a project from the sidebar to begin.
            </p>
          ) : tab === "configure" ? (
            <div className="mx-auto flex max-w-4xl flex-col gap-4">
              <RepoManager projectId={active.id} />
              <MetaFiles projectId={active.id} />
              <ModuleSelector
                projectId={active.id}
                selected={selected}
                onChange={setSelected}
              />
              <RunPanel
                projectId={active.id}
                selected={selected}
                onComplete={(runId) => {
                  setActiveRunId(runId);
                  setHistoryKey((k) => k + 1);
                  setTab("results");
                }}
              />
              <ChatBox projectId={active.id} />
            </div>
          ) : tab === "results" ? (
            <div className="mx-auto grid max-w-5xl grid-cols-[260px_1fr] gap-4">
              <RunHistory
                projectId={active.id}
                activeRunId={activeRunId}
                refreshKey={historyKey}
                onSelect={setActiveRunId}
              />
              <div className="h-[70vh] rounded-lg border border-cf-border bg-cf-surface">
                <ResultsView projectId={active.id} runId={activeRunId} />
              </div>
            </div>
          ) : (
            <div className="mx-auto h-[75vh] max-w-4xl">
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

  return <WorkspaceView status={status} onReset={() => setForceSetup(true)} />;
}
