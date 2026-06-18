import { useEffect, useState } from "react";
import { api, type IndexItem } from "../lib/ipc";
import { Spinner } from "./Spinner";

interface Props {
  open: boolean;
  projectId: string;
  itemId: string;
  onClose: () => void;
}

function pathFromItemId(itemId: string): string {
  if (itemId.startsWith("repo:")) return `repos/${itemId.slice(5)}`;
  if (itemId.startsWith("meta:")) return `meta/${itemId.slice(5)}`;
  if (itemId.startsWith("artifact:")) {
    const rest = itemId.slice("artifact:".length);
    return `runs/${rest}`;
  }
  return itemId;
}

export function IndexItemModal({ open, projectId, itemId, onClose }: Props) {
  const [item, setItem] = useState<IndexItem | null>(null);
  const [missingFromIndex, setMissingFromIndex] = useState(false);
  const [description, setDescription] = useState("");
  const [keywords, setKeywords] = useState<string[]>([]);
  const [keywordInput, setKeywordInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    (async () => {
      setLoading(true);
      setError(null);
      setMissingFromIndex(false);
      try {
        const found = await api.readIndexItem(projectId, itemId);
        setItem(found);
        setDescription(found.description ?? "");
        setKeywords(found.keywords ?? []);
      } catch {
        setMissingFromIndex(true);
        setItem({
          id: itemId,
          type: itemId.split(":")[0] ?? "unknown",
          path: pathFromItemId(itemId),
        });
        setDescription("");
        setKeywords([]);
      } finally {
        setLoading(false);
      }
    })();
  }, [open, projectId, itemId]);

  const addKeyword = () => {
    const k = keywordInput.trim().toLowerCase();
    if (!k || keywords.includes(k)) return;
    setKeywords((prev) => [...prev, k]);
    setKeywordInput("");
  };

  const save = async () => {
    setBusy("save");
    setError(null);
    try {
      await api.setIndexAnnotation(projectId, itemId, description.trim(), keywords);
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const regenerate = async () => {
    setBusy("regen");
    setError(null);
    try {
      await api.enrichIndexItem(projectId, itemId);
      const found = await api.readIndexItem(projectId, itemId);
      setItem(found);
      setMissingFromIndex(false);
      setDescription(found.description ?? "");
      setKeywords(found.keywords ?? []);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  if (!open) return null;

  const source = item?.source ?? "heuristic";
  const displayPath = item?.path && item.path !== itemId ? item.path : pathFromItemId(itemId);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-cf-bg/70">
      <div className="flex max-h-[85vh] w-[520px] flex-col rounded-lg border border-cf-border bg-cf-surface p-5">
        <div className="mb-3 flex items-start justify-between gap-2">
          <div>
            <h3 className="text-lg font-semibold text-cf-ink">Index entry</h3>
            <p className="font-mono text-xs text-cf-muted">{itemId}</p>
            <p className="text-xs text-cf-muted">{displayPath}</p>
          </div>
          <span className="rounded border border-cf-border px-2 py-0.5 text-xs text-cf-muted">
            {source === "user" ? "edited" : source}
          </span>
        </div>

        {loading ? (
          <div className="flex justify-center py-8">
            <Spinner />
          </div>
        ) : (
          <div className="flex-1 space-y-3 overflow-y-auto">
            {missingFromIndex && (
              <p className="rounded-md border border-cf-border bg-cf-surface-2 px-3 py-2 text-xs text-cf-muted">
                Index not built yet for this item. Use <strong>Refresh index</strong> on the
                Repositories tab, or save a manual description below.
              </p>
            )}

            <label className="block text-xs font-medium text-cf-muted">
              Description
              <textarea
                className="mt-1 w-full rounded-md border border-cf-border bg-cf-surface-2 px-2 py-1.5 text-sm text-cf-ink outline-none focus:border-cf-accent"
                rows={3}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </label>

            <div>
              <span className="text-xs font-medium text-cf-muted">Keywords</span>
              <div className="mt-1 flex flex-wrap gap-1">
                {keywords.map((k) => (
                  <span
                    key={k}
                    className="inline-flex items-center gap-1 rounded bg-cf-surface-2 px-2 py-0.5 text-xs text-cf-ink"
                  >
                    {k}
                    <button
                      type="button"
                      className="text-cf-muted hover:text-cf-danger"
                      onClick={() => setKeywords((prev) => prev.filter((x) => x !== k))}
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
              <div className="mt-2 flex gap-2">
                <input
                  className="flex-1 rounded-md border border-cf-border bg-cf-surface-2 px-2 py-1 text-sm text-cf-ink outline-none focus:border-cf-accent"
                  placeholder="add keyword"
                  value={keywordInput}
                  onChange={(e) => setKeywordInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addKeyword();
                    }
                  }}
                />
                <button
                  type="button"
                  className="rounded border border-cf-border px-2 py-1 text-xs text-cf-ink hover:bg-cf-surface-2"
                  onClick={addKeyword}
                >
                  Add
                </button>
              </div>
            </div>

            {error && <p className="text-xs text-cf-danger">{error}</p>}
          </div>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            className="rounded-md border border-cf-border px-3 py-1.5 text-sm text-cf-ink hover:bg-cf-surface-2"
            onClick={onClose}
            disabled={busy !== null}
          >
            Cancel
          </button>
          <button
            type="button"
            className="rounded-md border border-cf-border px-3 py-1.5 text-sm text-cf-ink hover:bg-cf-surface-2"
            onClick={() => void regenerate()}
            disabled={busy !== null || loading}
          >
            {busy === "regen" ? <Spinner size={12} /> : "Regenerate with AI"}
          </button>
          <button
            type="button"
            className="rounded-md bg-cf-accent px-3 py-1.5 text-sm font-medium text-cf-accent-ink hover:opacity-90"
            onClick={() => void save()}
            disabled={busy !== null || loading}
          >
            {busy === "save" ? <Spinner size={12} /> : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
