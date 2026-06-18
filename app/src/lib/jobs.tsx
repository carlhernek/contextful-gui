import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, onContextfulEvent, type SidecarEvent } from "./ipc";

export type JobKind = "run" | "index" | "clone" | "pull";

export interface Job {
  key: string;
  kind: JobKind;
  projectId: string;
  label: string;
  startedAt: string;
}

interface JobEventData {
  action: "started" | "finished" | "failed";
  job: Job;
}

interface JobsContextValue {
  activeJob: Job | null;
  isBusy: boolean;
  isBusyForProject: (projectId: string) => boolean;
  isKindBusy: (kind: JobKind, projectId?: string) => boolean;
  stop: () => Promise<void>;
  refresh: () => Promise<void>;
}

const JobsContext = createContext<JobsContextValue | null>(null);

function parseJobEvent(data: unknown): JobEventData | null {
  if (!data || typeof data !== "object") return null;
  const d = data as Record<string, unknown>;
  if (typeof d.action !== "string" || !d.job || typeof d.job !== "object") return null;
  const job = d.job as Job;
  if (!job.kind || !job.label) return null;
  return { action: d.action as JobEventData["action"], job };
}

export function JobsProvider({ children }: { children: ReactNode }) {
  const [activeJob, setActiveJob] = useState<Job | null>(null);

  const refresh = useCallback(async () => {
    const jobs = await api.listJobs();
    setActiveJob(jobs[0] ?? null);
  }, []);

  useEffect(() => {
    void refresh();
    let unlisten: (() => void) | undefined;
    onContextfulEvent((e: SidecarEvent) => {
      if (e.event !== "job") return;
      const parsed = parseJobEvent(e.data);
      if (!parsed) return;
      if (parsed.action === "started") {
        setActiveJob(parsed.job);
      } else {
        setActiveJob((current) =>
          current?.key === parsed.job.key ? null : current
        );
      }
    }).then((fn) => {
      unlisten = fn;
    });
    return () => unlisten?.();
  }, [refresh]);

  const stop = useCallback(async () => {
    await api.stopJob();
  }, []);

  const value = useMemo<JobsContextValue>(() => {
    const isBusy = activeJob !== null;
    return {
      activeJob,
      isBusy,
      isBusyForProject: (projectId: string) =>
        isBusy && activeJob?.projectId === projectId,
      isKindBusy: (kind: JobKind, projectId?: string) =>
        isBusy &&
        activeJob?.kind === kind &&
        (projectId === undefined || activeJob.projectId === projectId),
      stop,
      refresh,
    };
  }, [activeJob, stop, refresh]);

  return <JobsContext.Provider value={value}>{children}</JobsContext.Provider>;
}

export function useJobs(): JobsContextValue {
  const ctx = useContext(JobsContext);
  if (!ctx) throw new Error("useJobs must be used within JobsProvider");
  return ctx;
}

export function useJob(kind?: JobKind, projectId?: string) {
  const { activeJob, isBusy, isKindBusy, isBusyForProject, stop } = useJobs();
  const busy =
    kind !== undefined
      ? isKindBusy(kind, projectId)
      : projectId !== undefined
        ? isBusyForProject(projectId)
        : isBusy;
  return { busy, job: busy ? activeJob : null, stop, isBusy, activeJob };
}
