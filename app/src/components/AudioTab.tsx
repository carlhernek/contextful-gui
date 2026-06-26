import { useEffect, useState } from "react";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { api, type AudioFile } from "../lib/ipc";
import { useJob } from "../lib/jobs";
import { IndexButton } from "./IndexButton";
import { Spinner } from "./Spinner";

const AUDIO_EXTENSIONS = ["mp3", "wav", "m4a", "ogg", "flac", "aac", "aiff", "webm"];

function hasAudioExtension(path: string): boolean {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  return AUDIO_EXTENSIONS.includes(ext);
}

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
  const [dragOver, setDragOver] = useState(false);
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

  const addDroppedPaths = async (paths: string[]) => {
    const audioPaths = paths.filter(hasAudioExtension);
    if (audioPaths.length === 0) {
      setError(
        `No audio files in the drop. Supported types: ${AUDIO_EXTENSIONS.join(", ")}.`,
      );
      return;
    }
    setAdding(true);
    setError(null);
    try {
      await api.addAudioPaths(projectId, audioPaths);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setAdding(false);
    }
  };

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    let disposed = false;
    void getCurrentWebview()
      .onDragDropEvent((event) => {
        if (event.payload.type === "over" || event.payload.type === "enter") {
          setDragOver(true);
        } else if (event.payload.type === "drop") {
          setDragOver(false);
          void addDroppedPaths(event.payload.paths);
        } else {
          setDragOver(false);
        }
      })
      .then((fn) => {
        if (disposed) fn();
        else unlisten = fn;
      });
    return () => {
      disposed = true;
      unlisten?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

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
    <div className="relative mx-auto max-w-3xl">
      {dragOver && (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-lg border-2 border-dashed border-cf-accent bg-cf-accent/10 text-sm font-medium text-cf-accent">
          Drop audio files to add them to meta/audio/
        </div>
      )}
      <div className="mb-3 rounded-md border border-cf-border bg-cf-surface-2 px-3 py-2 text-xs text-cf-muted">
        Drag &amp; drop audio files here (or use <em>Add audio…</em>) to copy them into{" "}
        <code>meta/audio/</code>. Adding never transcribes — transcription runs only when you click{" "}
        <em>Transcribe pending</em> or a module needs it. Each transcript is saved as an indexed{" "}
        <code>.transcript.md</code> next to its audio (the raw audio stays out of the index). The
        transcription model is configured in Settings.
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
        <p className="text-sm text-cf-muted">
          No audio yet. Drag &amp; drop audio files anywhere here, or use “Add audio…”.
        </p>
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
