# Contextful App — Agent Conventions

## Busy jobs (mandatory for all long-running work)

Every operation that can take more than a moment **must** register with the central `JobManager` in Rust (`src-tauri/src/jobs.rs`). The UI reads job state via `list_jobs` + `contextful-event` `{ event: "job" }` and `useJobs()` / `useJob()` in `src/lib/jobs.tsx`.

### Rules

1. **Single-flight** — Only one job may be active app-wide (one sidecar worker). Starting a second job while one is running must **reject** with a clear error (`Contextful is busy with …`). Never queue duplicate work.
2. **Stoppable** — User-initiated jobs must be cancellable via `stop_job` → `SidecarManager::cancel()`.
3. **Survive tab switches** — UI components must **not** use local `useState` for run/clone/pull/index busy flags. Use `useJob(kind, projectId)` or the header `ActivityIndicator`.
4. **Auto/coalesced jobs** — Background index refresh after clone/pull/meta changes uses `try_begin_or_skip` (Index kind). If busy, skip and log to `.eventlog` — never queue.
5. **Adding a new worker**:
   - Add a `JobKind` variant in `jobs.rs`
   - Gate the Tauri command with `jobs.try_begin(...)` before sidecar/Rust blocking work
   - Hold `JobGuard` for the full operation; call `guard.fail()` on error
   - Wire UI via `useJob` and disable conflicting controls when `isBusy`

### Job kinds today

| Kind | Label example | Trigger |
|------|---------------|---------|
| `Run` | Running pipeline | `start_run` |
| `Index` | Indexing workspace | Auto after clone/pull/meta; internal `trigger_index_refresh` |
| `Clone` | Cloning repositories | `clone_repos` |
| `Pull` | Pulling repositories | `pull_repos` |

### Indexing

- Indexing is **fully automatic** — no manual Refresh or Regenerate-with-AI buttons.
- Users may **edit annotations** (description/keywords) via `IndexItemModal` → `set_index_annotation`.
- Optional on-demand rebuild: select **Workspace Index** module in Pipeline tab (deterministic, not LLM).

### Sidecar RPC lock

`SidecarManager::request()` holds `request_lock` so two RPCs never interleave on stdin.
