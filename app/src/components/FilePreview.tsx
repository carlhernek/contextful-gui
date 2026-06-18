import type { PreviewResult } from "../lib/ipc";
import { Spinner } from "./Spinner";

interface Props {
  preview: PreviewResult | null;
  loading: boolean;
}

export function FilePreview({ preview, loading }: Props) {
  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner size={20} />
      </div>
    );
  }

  if (!preview) {
    return (
      <p className="p-4 text-sm text-cf-muted">Select a file to preview.</p>
    );
  }

  if (!preview.ok) {
    return (
      <p className="p-4 text-sm text-cf-danger">{preview.error ?? "Preview unavailable"}</p>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-cf-border px-3 py-2 text-xs text-cf-muted">
        {preview.name ?? preview.path}
        {preview.truncated && " — truncated"}
      </div>
      <div className="flex-1 overflow-auto p-3">
        {preview.kind === "table" && preview.table ? (
          <div className="overflow-auto">
            <table className="w-full border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-cf-border">
                  {preview.table.headers.map((h, i) => (
                    <th key={i} className="px-2 py-1 font-medium text-cf-ink">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.table.rows.map((row, ri) => (
                  <tr key={ri} className="border-b border-cf-border/50">
                    {row.map((cell, ci) => (
                      <td key={ci} className="px-2 py-1 text-cf-muted">
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <pre className="whitespace-pre-wrap font-mono text-xs text-cf-ink">{preview.content}</pre>
        )}
      </div>
    </div>
  );
}
