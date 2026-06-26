import { useEffect, useState } from "react";
import { api, type AudioFile } from "../lib/ipc";
import { useJob } from "../lib/jobs";
import { IndexButton } from "./IndexButton";
import { Spinner } from "./Spinner";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function transcriptItemId(transcriptPath: string): string {
  return `meta:${transcriptPath.replace(/^meta\//, "")}`;
}

export function AudioTab({ projectId }: { projectId: string }) {
  const [audio, setAudio] = useState<AudioFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { busy: transcribeBusy } = useJob("transcribe", projectId);
  const { isBusy } = useJob(undefined, projectId);
  const busy = transcribeBusy || isBusy;

  const refresh = async () => {
    setLoading(true);
    try {
      const res = await api.listAudio(projectId);
      setAudio(res.audio);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const addFiles = async () => {
    setAdding(true);
    setError(null);
    try {
      await api.addAudioFiles(projectId);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setAdding(false);
    }
  };

  const transcribe = async () => {
    setError(null);
    try {
      await api.transcribeAudio(projectId);
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  const pendingCount = audio.filter((a) => !a.transcribed).length;

  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-3 rounded-md border border-cf-border bg-cf-surface-2 px-3 py-2 text-xs text-cf-muted">
        Audio files are meta documents stored under <code>meta/audio/</code>. Transcription uses
        OpenRouter and only processes audio that hasn&apos;t been transcribed before; each transcript
        is saved as an indexed <code>.transcript.md</code> next to its audio, and the raw audio is
        kept out of the index in favor of the transcript. The transcription model is configured in
        Settings.
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="flex items-center gap-2 rounded-md border border-cf-border px-3 py-1.5 text-sm text-cf-ink hover:bg-cf-surface disabled:opacity-40"
          onClick={() => void addFiles()}
          disabled={adding || busy}
        >
          {adding ? <Spinner size={12} /> : null} Add audio…
        </button>
        <button
          type="button"
          className="flex items-center gap-2 rounded-md bg-cf-accent px-3 py-1.5 text-sm font-medium text-cf-accent-ink hover:opacity-90 disabled:opacity-40"
          onClick={() => void transcribe()}
          disabled={busy || pendingCount === 0}
          title={pendingCount === 0 ? "Nothing pending to transcribe" : undefined}
        >
          {transcribeBusy ? <Spinner size={12} /> : null} Transcribe pending
          {pendingCount > 0 ? ` (${pendingCount})` : ""}
        </button>
        <button
          type="button"
          className="text-xs text-cf-muted hover:underline disabled:opacity-40"
          onClick={() => void refresh()}
          disabled={loading || busy}
        >
          refresh
        </button>
      </div>

      {audio.length === 0 && !loading && (
        <p className="text-sm text-cf-muted">No audio yet. Add audio files to get started.</p>
      )}

      <div className="space-y-1">
        {audio.map((a) => (
          <div
            key={a.path}
            className="flex items-center justify-between rounded-md bg-cf-surface-2 px-3 py-2 text-sm"
          >
            <div className="min-w-0">
              <span className="truncate text-cf-ink">{a.name}</span>
              <span className="ml-2 text-xs text-cf-muted">{formatSize(a.size)}</span>
              <div className="text-xs text-cf-muted">
                {a.transcribed ? (
                  <>
                    transcribed
                    {a.transcribedAt ? ` ${new Date(a.transcribedAt).toLocaleString()}` : ""}
                    {a.model ? ` · ${a.model}` : ""}
                  </>
                ) : (
                  "pending transcription"
                )}
              </div>
            </div>
            <div className="flex items-center gap-2">
              {a.transcribed ? (
                <span className="rounded bg-cf-success/15 px-1.5 py-0.5 text-xs text-cf-success">
                  ✓ transcript
                </span>
              ) : (
                <span className="rounded bg-cf-surface px-1.5 py-0.5 text-xs text-cf-muted">
                  pending
                </span>
              )}
              {a.transcribed && a.transcriptPath && (
                <IndexButton
                  projectId={projectId}
                  itemId={transcriptItemId(a.transcriptPath)}
                  disabled={busy}
                />
              )}
            </div>
          </div>
        ))}
      </div>

      {error && <pre className="mt-3 whitespace-pre-wrap text-xs text-cf-danger">{error}</pre>}
    </div>
  );
}
