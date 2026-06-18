import { Spinner } from "./Spinner";

export function BusyOverlay({ show, message }: { show: boolean; message?: string }) {
  if (!show) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-cf-bg/70 backdrop-blur-sm">
      <div className="flex items-center gap-3 rounded-lg border border-cf-border bg-cf-surface px-5 py-4">
        <Spinner size={20} />
        <span className="text-sm text-cf-ink">{message ?? "Working…"}</span>
      </div>
    </div>
  );
}
