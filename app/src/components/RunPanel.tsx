import { useEffect, useRef, useState } from "react";
import { api, onContextfulEvent, type RunState } from "../lib/ipc";
import { statusBannerClass } from "../lib/statusStyles";
import { Spinner } from "./Spinner";

const STALL_HINT_MS = 45_000;
const STALL_WARN_MS = 120_000;
const STALL_TICK_MS = 5_000;
const RUN_POLL_MS = 15_000;

const LIVE_EVENTS = new Set(["token", "module", "turn", "tool", "activity", "heartbeat"]);

interface Props {
  projectId: string;
  selected: string[];
  onComplete: (runId: string) => void;
}

export function RunPanel({ projectId, selected, onComplete }: Props) {
  const [running, setRunning] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [currentModule, setCurrentModule] = useState<string | null>(null);
  const [turn, setTurn] = useState<{ turn: number; maxTurns: number } | null>(null);
  const [completed, setCompleted] = useState<string[]>([]);
  const [stall, setStall] = useState<string | null>(null);
  const [result, setResult] = useState<RunState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [instructions, setInstructions] = useState("");
  const [force, setForce] = useState(false);

  const lastActivity = useRef<number>(Date.now());

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    onContextfulEvent((e) => {
      if (LIVE_EVENTS.has(e.event)) {
        lastActivity.current = Date.now();
        setStall(null);
      }
      if (e.event === "module") {
        const d = e.data as { module: string; status: string; summary?: string };
        if (d.status === "START") setCurrentModule(d.module);
      } else if (e.event === "turn") {
        const d = e.data as { turn: number; maxTurns: number };
        setTurn({ turn: d.turn, maxTurns: d.maxTurns });
      } else if (e.event === "run") {
        const d = e.data as { completedModules: string[] };
        setCompleted(d.completedModules ?? []);
      }
    }).then((fn) => (unlisten = fn));
    return () => unlisten?.();
  }, []);

  // Stall detection + run-state poll backstop.
  useEffect(() => {
    if (!running) return;
    const stallTimer = setInterval(() => {
      const idle = Date.now() - lastActivity.current;
      if (idle > STALL_WARN_MS) setStall("Agent may be stuck");
      else if (idle > STALL_HINT_MS) setStall("Still working…");
    }, STALL_TICK_MS);
    const pollTimer = setInterval(async () => {
      if (runId) {
        try {
          const s = await api.getRunState(projectId, runId);
          setCompleted(s.completedModules);
        } catch {
          /* ignore */
        }
      }
    }, RUN_POLL_MS);
    return () => {
      clearInterval(stallTimer);
      clearInterval(pollTimer);
    };
  }, [running, runId, projectId]);

  const start = async () => {
    if (!selected.length) return;
    setError(null);
    setResult(null);
    setCompleted([]);
    setCurrentModule(null);
    setTurn(null);
    lastActivity.current = Date.now();
    const id = await api.newRunId();
    setRunId(id);
    setRunning(true);
    try {
      const state = await api.startRun({
        id: projectId,
        runId: id,
        modules: selected,
        force,
        resume: true,
        specificInstructions: instructions.trim() || undefined,
      });
      setResult(state);
      onComplete(id);
    } catch (e) {
      setError(String(e));
    } finally {
      setRunning(false);
      setStall(null);
      setCurrentModule(null);
    }
  };

  const stop = async () => {
    await api.stopRun();
  };

  return (
    <div className="rounded-lg border border-cf-border bg-cf-surface p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-semibold text-cf-ink">Run</h3>
        <label className="flex items-center gap-1 text-xs text-cf-muted">
          <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
          force re-run
        </label>
      </div>

      <textarea
        className="mb-3 h-16 w-full resize-none rounded-md border border-cf-border bg-cf-surface-2 px-2 py-1.5 text-sm text-cf-ink outline-none focus:border-cf-accent"
        placeholder="Optional: specific instructions for this run…"
        value={instructions}
        onChange={(e) => setInstructions(e.target.value)}
        disabled={running}
      />

      <div className="flex items-center gap-2">
        {!running ? (
          <button
            className="rounded-md bg-cf-success px-4 py-1.5 text-sm font-medium text-cf-bg hover:opacity-90 disabled:opacity-40"
            disabled={!selected.length}
            onClick={start}
          >
            Start run ({selected.length} modules)
          </button>
        ) : (
          <button
            className="rounded-md bg-cf-danger px-4 py-1.5 text-sm font-medium text-white hover:opacity-90"
            onClick={stop}
          >
            Stop
          </button>
        )}
        {running && <Spinner size={14} />}
      </div>

      {running && (
        <div className="mt-3 space-y-1 text-sm">
          {currentModule && (
            <div className="text-cf-info">
              Running <span className="font-medium">{currentModule}</span>
              {turn && ` — turn ${turn.turn}/${turn.maxTurns}`}
            </div>
          )}
          <div className="text-cf-muted">Completed: {completed.join(", ") || "none yet"}</div>
          {stall && <div className="text-cf-warning">{stall}</div>}
        </div>
      )}

      {result && (
        <div className={`mt-3 rounded-md border px-3 py-2 text-sm ${statusBannerClass(result.status)}`}>
          Run {result.runId}: {result.status}
          {result.error ? ` — ${result.error}` : ""}
        </div>
      )}

      {error && <p className="mt-3 text-sm text-cf-danger">{error}</p>}
    </div>
  );
}
