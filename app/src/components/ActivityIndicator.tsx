import { useJobs } from "../lib/jobs";
import { Spinner } from "./Spinner";

export function ActivityIndicator() {
  const { activeJob, isBusy, stop } = useJobs();

  if (!isBusy || !activeJob) return null;

  return (
    <div className="flex items-center gap-2 rounded-md border border-cf-border bg-cf-surface-2 px-3 py-1.5 text-xs text-cf-ink">
      <Spinner size={12} />
      <span>{activeJob.label}</span>
      <button
        type="button"
        className="rounded border border-cf-border px-2 py-0.5 text-cf-muted hover:bg-cf-surface hover:text-cf-ink"
        onClick={() => void stop()}
      >
        Stop
      </button>
    </div>
  );
}
