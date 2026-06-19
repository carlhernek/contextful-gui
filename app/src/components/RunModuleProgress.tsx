import { statusTextClass } from "../lib/statusStyles";
import type { ModuleStage, ModuleStageStatus } from "../lib/runProgress";
import { Spinner } from "./Spinner";

const STATUS_LABEL: Record<ModuleStageStatus, string> = {
  complete: "complete",
  running: "running",
  pending: "pending",
  failed: "failed",
  skipped: "skipped",
};

const STATUS_DOT: Record<ModuleStageStatus, string> = {
  complete: "bg-cf-success",
  running: "bg-cf-info",
  pending: "bg-cf-border",
  failed: "bg-cf-danger",
  skipped: "bg-cf-warning/60",
};

interface Props {
  stages: ModuleStage[];
  compact?: boolean;
  turn?: { turn: number; maxTurns: number } | null;
}

export function RunModuleProgress({ stages, compact = false, turn }: Props) {
  if (stages.length <= 1) return null;

  return (
    <ul className={`space-y-1 ${compact ? "text-xs" : "text-sm"}`}>
      {stages.map((stage) => {
        const isRunning = stage.status === "running";
        return (
          <li key={stage.id} className="flex min-w-0 items-center gap-2">
            {isRunning ? (
              <Spinner size={compact ? 10 : 12} />
            ) : (
              <span
                className={`h-2 w-2 shrink-0 rounded-full ${STATUS_DOT[stage.status]}`}
                aria-hidden
              />
            )}
            <span className="min-w-0 truncate font-mono text-cf-ink">{stage.id}</span>
            <span className={`shrink-0 ${statusTextClass(stage.status)}`}>
              {STATUS_LABEL[stage.status]}
              {isRunning && turn ? ` · turn ${turn.turn}/${turn.maxTurns}` : ""}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
