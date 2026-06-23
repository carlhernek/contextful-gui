import { ModuleSelector } from "./ModuleSelector";
import { RunHistory } from "./RunHistory";
import { RunPanel } from "./RunPanel";
import type { RunState } from "../lib/ipc";

interface Props {
  projectId: string;
  selected: string[];
  onChangeSelected: (modules: string[]) => void;
  onRunStart?: (runId: string) => void;
  onComplete: (runId: string) => void;
  onSelectRun?: (runId: string) => void;
  onResumeRun?: (state: RunState) => void;
  activeRunId?: string | null;
  historyKey?: number;
  modulesRefreshKey?: number;
}

export function PipelineTab({
  projectId,
  selected,
  onChangeSelected,
  onRunStart,
  onComplete,
  onSelectRun,
  onResumeRun,
  activeRunId = null,
  historyKey = 0,
  modulesRefreshKey,
}: Props) {
  return (
    <div className="mx-auto grid max-w-5xl grid-cols-[260px_minmax(0,1fr)] gap-4">
      <RunHistory
        projectId={projectId}
        activeRunId={activeRunId}
        refreshKey={historyKey}
        onSelect={(runId) => onSelectRun?.(runId)}
        onResume={onResumeRun}
      />
      <div className="flex flex-col gap-4">
        <ModuleSelector
          projectId={projectId}
          selected={selected}
          onChange={onChangeSelected}
          refreshKey={modulesRefreshKey}
        />
        <RunPanel
          projectId={projectId}
          selected={selected}
          onRunStart={onRunStart}
          onComplete={onComplete}
        />
      </div>
    </div>
  );
}
