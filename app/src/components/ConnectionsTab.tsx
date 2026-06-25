import { GitConnectionsPanel } from "./GitConnectionsPanel";
import { SupabaseConnectionsPanel } from "./SupabaseConnectionsPanel";

export function ConnectionsTab({ projectId }: { projectId: string }) {
  return (
    <div className="mx-auto max-w-4xl rounded-lg border border-cf-border bg-cf-surface p-4">
      <GitConnectionsPanel projectId={projectId} />
      <SupabaseConnectionsPanel projectId={projectId} />
    </div>
  );
}
