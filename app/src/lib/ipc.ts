import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

export interface SidecarEvent {
  id: string | null;
  event: string;
  data: unknown;
}

export interface PrereqStatus {
  git: boolean;
  git_path: string | null;
  python: boolean;
  python_path: string | null;
  ripgrep: boolean;
  network: boolean;
}

export interface SetupStatus {
  hasApiKey: boolean;
  maskedApiKey: string | null;
  templateReady: boolean;
  installPath: string | null;
  activeProject: string | null;
}

export interface Project {
  id: string;
  display_name: string;
  project_type: string;
  repos: RepoEntry[];
}

export interface RepoEntry {
  name: string;
  url: string;
  branch: string;
}

export interface RepoStatus {
  name: string;
  url: string;
  branch: string;
  cloned: boolean;
  head?: string | null;
}

export interface GitCredentialHost {
  host: string;
  configured: boolean;
  masked?: string | null;
}

export interface MetaEntry {
  name: string;
  path: string;
  kind: "file" | "dir";
  size?: number;
}

export interface ChatMessage {
  role: "user" | "assistant" | string;
  content: string;
  ts: string;
}

export interface PreviewTable {
  headers: string[];
  rows: string[][];
}

export interface PreviewResult {
  ok: boolean;
  error?: string;
  path: string;
  name?: string;
  ext?: string;
  size?: number;
  truncated?: boolean;
  kind?: "text" | "table" | "image" | "unsupported";
  content?: string;
  table?: PreviewTable;
  imageUrl?: string;
}

export interface ModuleInfo {
  id: string;
  title: string;
  packs: string[];
}

export interface RunState {
  runId: string;
  status: "idle" | "running" | "failed" | "cancelled" | "complete";
  completedModules: string[];
  plannedModules?: string[];
  failedModule?: string | null;
  error?: string | null;
  updatedAt?: string | null;
}

export interface Task {
  id: string;
  title: string;
  priority: "high" | "medium" | "low";
  effort: "S" | "M" | "L";
  evidence: string[];
  rationale: string;
  agentic_spec: string;
}

export interface TasksDoc {
  moduleId: string;
  runId: string;
  tasks: Task[];
}

export interface ModuleArtifact {
  moduleId: string;
  hasAnalysis: boolean;
  hasActivity?: boolean;
  analysis?: string | null;
  tasks: TasksDoc | null;
}

export interface ActivityEntry {
  seq: number;
  ts: string;
  kind: string;
  turn?: number;
  maxTurns?: number;
  text?: string;
  name?: string;
  args?: Record<string, unknown>;
  result?: string;
  itemId?: string;
  itemIndex?: number;
  itemTotal?: number;
  path?: string;
  status?: string;
  description?: string;
  keywords?: string[];
  durationMs?: number;
  finishReason?: string;
  toolCallCount?: number;
  contentLength?: number;
  itemCount?: number;
}

export interface RunActivity {
  entries: ActivityEntry[];
}

export interface RunArtifacts {
  runId: string;
  modules: ModuleArtifact[];
  summary: string | null;
}

export interface VersionStatus {
  local: string;
  remote: string;
  updateAvailable: boolean;
}

export interface IndexItem {
  id: string;
  type: "repo" | "meta" | "artifact" | string;
  path: string;
  description?: string;
  keywords?: string[];
  source?: "user" | "ai" | "heuristic" | string;
  meta?: Record<string, unknown>;
}

export interface WorkspaceIndex {
  version: number;
  updatedAt?: string | null;
  project?: { displayName?: string; projectType?: string };
  items: IndexItem[];
}

export interface JobInfo {
  key: string;
  kind: "run" | "index" | "clone" | "pull";
  projectId: string;
  label: string;
  startedAt: string;
}

/** Listen for streamed sidecar events; returns an unlisten fn. */
export async function onContextfulEvent(handler: (e: SidecarEvent) => void) {
  return listen<SidecarEvent>("contextful-event", (e) => handler(e.payload));
}

// ---- typed command wrappers ----
export const api = {
  checkPrereqs: () => invoke<PrereqStatus>("check_prereqs"),
  installPrereqs: () => invoke<string>("install_prereqs"),
  getSetupStatus: () => invoke<SetupStatus>("get_setup_status"),
  defaultInstallFolder: () => invoke<string>("default_install_folder"),
  pickInstallFolder: () => invoke<string | null>("pick_install_folder"),
  setInstallPath: (path: string) => invoke<SetupStatus>("set_install_path", { path }),
  setupTemplate: () => invoke<unknown>("setup_template"),

  setApiKey: (key: string) => invoke<void>("set_api_key", { key }),
  clearApiKey: () => invoke<void>("clear_api_key"),
  storedApiKeyMasked: () => invoke<string | null>("stored_api_key_masked"),

  listGitCredentialHosts: (id: string) =>
    invoke<{ hosts: GitCredentialHost[] }>("list_git_credential_hosts", { id }),
  setGitCredential: (host: string, token: string) =>
    invoke<void>("set_git_credential", { host, token }),
  clearGitCredential: (host: string) => invoke<void>("clear_git_credential", { host }),

  getSettings: () => invoke<SetupStatus>("get_settings"),
  setModels: (projectId: string, models: Record<string, string>) =>
    invoke<void>("set_models", { projectId, models }),
  listModels: () => invoke<{ models: unknown[] }>("list_models"),

  suggestProjectId: (displayName: string) =>
    invoke<string>("suggest_project_id", { displayName }),
  listProjects: () => invoke<Project[]>("list_projects"),
  createProject: (id: string, displayName: string) =>
    invoke<string>("create_project", { id, displayName }),
  renameProject: (id: string, displayName: string) =>
    invoke<void>("rename_project", { id, displayName }),
  deleteProject: (id: string) => invoke<void>("delete_project", { id }),
  setActiveProject: (id: string | null) => invoke<SetupStatus>("set_active_project", { id }),
  setProjectType: (id: string, projectType: string) =>
    invoke<void>("set_project_type", { id, projectType }),

  addRepo: (id: string, name: string, url: string, branch: string) =>
    invoke<void>("add_repo", { id, name, url, branch }),
  removeRepo: (id: string, name: string) => invoke<void>("remove_repo", { id, name }),
  cloneRepos: (id: string) => invoke<{ results: unknown[] }>("clone_repos", { id }),
  pullRepos: (id: string) => invoke<{ results: unknown[] }>("pull_repos", { id }),
  listRepos: (id: string) => invoke<RepoStatus[]>("list_repos", { id }),

  uploadMetaFiles: (id: string, sources: string[], destPath?: string) =>
    invoke<string[]>("upload_meta_files", { id, sources, destPath: destPath ?? null }),
  createMetaDir: (id: string, path: string) => invoke<void>("create_meta_dir", { id, path }),
  renameMetaEntry: (id: string, path: string, newName: string) =>
    invoke<string>("rename_meta_entry", { id, path, newName }),
  moveMetaEntry: (id: string, path: string, destPath: string) =>
    invoke<string>("move_meta_entry", { id, path, destPath }),
  listMetaDir: (id: string, relPath?: string) =>
    invoke<MetaEntry[]>("list_meta_dir", { id, relPath: relPath ?? null }),
  listMetaFiles: (id: string) => invoke<{ name: string; size: number }[]>("list_meta_files", { id }),
  deleteMetaEntry: (id: string, path: string) =>
    invoke<void>("delete_meta_entry", { id, path }),
  deleteMetaFile: (id: string, name: string) =>
    invoke<void>("delete_meta_file", { id, name }),

  getChatlog: (id: string) => invoke<ChatMessage[]>("get_chatlog", { id }),

  listModules: (id: string) => invoke<ModuleInfo[]>("list_modules", { id }),
  getModuleSuggestions: (id: string) => invoke<string[]>("get_module_suggestions", { id }),
  getModuleSelection: (id: string) => invoke<string[]>("get_module_selection", { id }),
  setModuleSelection: (id: string, modules: string[]) =>
    invoke<string[]>("set_module_selection", { id, modules }),

  getEventLog: (id: string) => invoke<string>("get_event_log", { id }),
  getRunLog: (id: string, runId: string) => invoke<string>("get_run_log", { id, runId }),
  getRunState: (id: string, runId: string) =>
    invoke<RunState>("get_run_state", { id, runId }),
  listRuns: (id: string) => invoke<RunState[]>("list_runs", { id }),
  getRunArtifacts: (id: string, runId: string) =>
    invoke<RunArtifacts>("get_run_artifacts", { id, runId }),
  getRunActivity: (id: string, runId: string, moduleId: string) =>
    invoke<RunActivity>("get_run_activity", { id, runId, moduleId }),

  configureSidecar: (projectId?: string) =>
    invoke<void>("configure_sidecar", { projectId: projectId ?? null }),
  sidecarHealth: () => invoke<{ ok: boolean; error?: string }>("sidecar_health"),
  sendChat: (id: string, message: string) =>
    invoke<{ type: string; reply: string; modules?: string[]; force?: boolean }>("send_chat", {
      id,
      message,
    }),

  newRunId: () => invoke<string>("new_run_id"),
  startRun: (args: {
    id: string;
    runId: string;
    modules: string[];
    force: boolean;
    forceReindex?: boolean;
    resume: boolean;
    specificInstructions?: string;
  }) =>
    invoke<RunState>("start_run", {
      id: args.id,
      runId: args.runId,
      modules: args.modules,
      force: args.force,
      forceReindex: args.forceReindex ?? false,
      resume: args.resume,
      specificInstructions: args.specificInstructions ?? null,
    }),
  stopRun: () => invoke<void>("stop_run"),
  stopJob: () => invoke<void>("stop_job"),
  listJobs: () => invoke<JobInfo[]>("list_jobs"),

  pullProjectModules: (id: string) => invoke<{ ok: boolean; version: string }>("pull_project_modules", { id }),
  getModulesVersionStatus: (id: string, fetch: boolean) =>
    invoke<VersionStatus>("get_modules_version_status", { id, fetch }),
  previewFile: (id: string, path: string, base?: string) =>
    invoke<PreviewResult>("preview_file", { id, path, base: base ?? "repos" }),

  getIndex: (id: string) => invoke<WorkspaceIndex>("get_index", { id }),
  readIndexItem: (id: string, itemId: string) =>
    invoke<IndexItem>("read_index_item", { id, itemId }),
  setIndexAnnotation: (id: string, itemId: string, description: string, keywords: string[]) =>
    invoke<{ description: string; keywords: string[] }>("set_index_annotation", {
      id,
      itemId,
      description,
      keywords,
    }),
};
