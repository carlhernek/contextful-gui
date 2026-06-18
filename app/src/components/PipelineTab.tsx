import { ModuleSelector } from "./ModuleSelector";
import { RunPanel } from "./RunPanel";

interface Props {
  projectId: string;
  selected: string[];
  onChangeSelected: (modules: string[]) => void;
  onComplete: (runId: string) => void;
  modulesRefreshKey?: number;
}

export function PipelineTab({
  projectId,
  selected,
  onChangeSelected,
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
      <RunPanel projectId={projectId} selected={selected} onComplete={onComplete} />
    </div>
  );
}
