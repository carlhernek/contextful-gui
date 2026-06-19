import { ModuleSelector } from "./ModuleSelector";
import { RunPanel } from "./RunPanel";

interface Props {
  projectId: string;
  selected: string[];
  onChangeSelected: (modules: string[]) => void;
  onRunStart?: (runId: string) => void;
  onComplete: (runId: string) => void;
  modulesRefreshKey?: number;
}

export function PipelineTab({
  projectId,
  selected,
  onChangeSelected,
  onRunStart,
  onComplete,
  modulesRefreshKey,
}: Props) {
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
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
  );
}
