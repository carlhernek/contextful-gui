# Contextful App — Agent Conventions

## Busy jobs (mandatory for all long-running work)

Every operation that can take more than a moment **must** register with the central `JobManager` in Rust (`src-tauri/src/jobs.rs`). The UI reads job state via `list_jobs` + `contextful-event` `{ event: "job" }` and `useJobs()` / `useJob()` in `src/lib/jobs.tsx`.

### Rules

1. **Single-flight** — Only one job may be active app-wide (one sidecar worker). Starting a second job while one is running must **reject** with a clear error (`Contextful is busy with …`). Never queue duplicate work.
2. **Stoppable** — User-initiated jobs must be cancellable via `stop_job` → `SidecarManager::cancel()`.
3. **Survive tab switches** — UI components must **not** use local `useState` for run/clone/pull/index busy flags. Use `useJob(kind, projectId)` or the header `ActivityIndicator`.
4. **No automatic indexing** — Do not trigger index scan/enrich after clone, pull, meta changes, or pipeline runs. Agentic indexing happens **only** when the user runs the **Workspace Index** module in the Pipeline tab.
5. **Manual annotations** — `set_index_annotation` patches `.workspace-index.json` in Rust directly (no sidecar RPC, no LLM).
6. **Event log** — Every user-visible operation (git clone/pull, job start/finish/cancel, index refresh, annotation edits) MUST append to `{project}/.eventlog` via `workspace::append_eventlog` (Rust) or `append_eventlog` (sidecar). UI job events are not enough on their own. **Every failure path** must append an `ERROR` line (scope matches the operation: `git`, `run`, `index`, `job`, etc.) with a short reason — not only success/cancel paths.
7. **Adding a new worker**:
   - Add a `JobKind` variant in `jobs.rs`
   - Gate the Tauri command with `jobs.try_begin(...)` before sidecar/Rust blocking work
   - Hold `JobGuard` for the full operation; call `guard.fail()` on error
   - Wire UI via `useJob` and disable conflicting controls when `isBusy`

### Job kinds today

| Kind | Label example | Trigger |
|------|---------------|---------|
| `Run` | Running pipeline | `start_run` |
| `Index` | (reserved — not used for auto jobs) | — |
| `Clone` | Cloning repositories | `clone_repos` |
| `Pull` | Pulling repositories | `pull_repos` |

### Indexing

- Indexing is **explicit only** — run the **Workspace Index** module in the Pipeline tab. No automatic refresh on clone, pull, meta changes, or other modules.
- Users may **edit annotations** (description/keywords) via `IndexItemModal` → `set_index_annotation` (direct `.workspace-index.json` patch, no LLM).
- The Workspace Index module is deterministic scan + optional LLM enrich (not a full agent loop).

### Sidecar RPC lock

`SidecarManager::request()` holds `request_lock` so two RPCs never interleave on stdin.
