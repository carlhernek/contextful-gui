"""Run orchestration: sequential modules, retry/backoff, resumable state (spec section 9)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from contextful_sidecar.runtime.agent import run_agent
from contextful_sidecar.runtime.agent_state import clear_agent_state
from contextful_sidecar.runtime.eventlog import append_eventlog
from contextful_sidecar.runtime.indexing import agentic_reindex
from contextful_sidecar.runtime.module_config import get_max_turns
from contextful_sidecar.runtime.openrouter import OpenRouterClient
from contextful_sidecar.runtime.tool_skips import read_skips

EventCallback = Callable[[str, Any], None]
CancelCheck = Callable[[], bool]

MAX_MODULE_RETRIES = 2          # 3 attempts total
RETRY_BASE_DELAY_SEC = 2.0      # exponential, capped at 15s
RETRY_MAX_DELAY_SEC = 15.0
_TRANSIENT_MARKERS = ("ssl", "certificate", "timeout", "timed out", "connection",
                      "network", "rate limit", "503", "502", "504", "429", "disconnect")

RUN_STATE_FILE = ".run-state.json"
TEMPLATE_VERSION_FILE = "modules/template-version.txt"
WORKSPACE_INDEX_MODULE = "workspace-index"


def _is_transient(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _TRANSIENT_MARKERS)


def format_exception(exc: BaseException) -> str:
    """Stable error text for logs when str(exc) is empty (e.g. some transport errors)."""
    text = str(exc).strip()
    if text:
        return text
    name = type(exc).__name__
    return f"{name} (no message)"


# --- run state ------------------------------------------------------------
def _run_dir(workspace: Path, run_id: str) -> Path:
    return Path(workspace) / "runs" / run_id


def _state_path(workspace: Path, run_id: str) -> Path:
    return _run_dir(workspace, run_id) / RUN_STATE_FILE


def load_run_state(workspace: Path, run_id: str) -> dict[str, Any]:
    path = _state_path(workspace, run_id)
    if not path.exists():
        return {
            "runId": run_id, "status": "idle", "completedModules": [],
            "failedModule": None, "error": None, "updatedAt": None,
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"runId": run_id, "status": "idle", "completedModules": []}


def save_run_state(workspace: Path, run_id: str, **updates: Any) -> dict[str, Any]:
    prev = load_run_state(workspace, run_id)
    state = dict(prev)
    state.update(updates)
    state["runId"] = run_id
    if state.get("status") == "failed":
        err = state.get("error")
        if not (isinstance(err, str) and err.strip()):
            failed = state.get("failedModule")
            state["error"] = f"{failed} failed" if failed else "Run failed"
    state["updatedAt"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    new_status = state.get("status")
    old_status = prev.get("status")
    if new_status and new_status != old_status:
        append_eventlog(
            workspace,
            "run",
            "STATE",
            f"runId={run_id} {old_status or 'idle'} -> {new_status}",
        )
    path = _state_path(workspace, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def template_version(workspace: Path) -> str:
    path = Path(workspace) / TEMPLATE_VERSION_FILE
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


# --- module filtering -----------------------------------------------------
def filter_modules(workspace: Path, run_id: str, selected: list[str],
                   resume: bool, force: bool) -> dict[str, Any]:
    state = load_run_state(workspace, run_id)
    completed = list(state.get("completedModules", []))
    status = state.get("status", "idle")

    if force:
        save_run_state(workspace, run_id, status="idle", completedModules=[],
                       failedModule=None, error=None)
        return {"to_run": _order_modules(list(selected)), "alreadyComplete": False}

    if resume and status in {"failed", "cancelled", "running", "complete"}:
        to_run = [m for m in selected if m not in completed]
        if not to_run:
            return {"to_run": [], "alreadyComplete": True}
        return {"to_run": _order_modules(to_run), "alreadyComplete": False}

    return {"to_run": _order_modules(list(selected)), "alreadyComplete": False}


def _order_modules(modules: list[str]) -> list[str]:
    """Run workspace-index last so analysis modules finish before index rebuild."""
    regular = [m for m in modules if m != WORKSPACE_INDEX_MODULE]
    if WORKSPACE_INDEX_MODULE in modules:
        regular.append(WORKSPACE_INDEX_MODULE)
    return regular


# --- module discovery -----------------------------------------------------
def _module_skill(workspace: Path, module_id: str) -> Path:
    return Path(workspace) / "modules" / module_id / "SKILL.md"


def _role_for(module_id: str) -> str:
    return module_id.replace("-", " ").title()


def _list_repo_paths(workspace: Path) -> list[Path]:
    repos = Path(workspace) / "repos"
    if not repos.exists():
        return []
    return sorted(p for p in repos.iterdir() if p.is_dir())


def _list_meta_docs(workspace: Path) -> list[Path]:
    meta = Path(workspace) / "meta"
    if not meta.exists():
        return []
    out: list[Path] = []
    for p in sorted(meta.rglob("*")):
        if p.is_file():
            out.append(p)
    return out


def _cancel_run(
    ws: Path,
    run_id: str,
    *,
    app_version: str,
    completed: list[str],
    on_event: EventCallback,
) -> dict[str, Any]:
    save_run_state(ws, run_id, status="cancelled")
    append_eventlog(ws, "run", "CANCELLED", f"runId={run_id} app=v{app_version}")
    on_event("run", {"runId": run_id, "status": "cancelled", "completedModules": completed})
    return {"runId": run_id, "status": "cancelled", "completedModules": completed}


async def _run_modules_body(
    *,
    ws: Path,
    client: OpenRouterClient,
    models: dict[str, str],
    run_id: str,
    modules: list[str],
    project_type: str,
    resume: bool,
    force: bool,
    force_reindex: bool = False,
    specific_instructions: str | None,
    on_event: EventCallback,
    should_cancel: CancelCheck,
    app_version: str,
) -> dict[str, Any]:
    version = template_version(ws)
    plan = filter_modules(ws, run_id, modules, resume, force)
    to_run = plan["to_run"]
    if plan.get("alreadyComplete"):
        return {"runId": run_id, "status": "complete", "completedModules":
                load_run_state(ws, run_id).get("completedModules", []), "alreadyComplete": True}

    planned = _order_modules(modules)
    prev = load_run_state(ws, run_id)
    if force or not prev.get("plannedModules"):
        planned_modules: list[str] = planned
    else:
        planned_modules = list(prev["plannedModules"])

    prev_status = prev.get("status")
    is_resuming = resume and prev_status in {"failed", "cancelled"}

    start_msg = f"runId={run_id} app=v{app_version} modules={version} ({len(to_run)} to run)"
    if force_reindex:
        start_msg += " forceReindex=true"
    append_eventlog(
        ws,
        "run",
        "START",
        start_msg,
    )
    state = save_run_state(
        ws, run_id, status="running", error=None, failedModule=None,
        plannedModules=planned_modules,
    )
    completed = list(state.get("completedModules", []))
    on_event("run", {"runId": run_id, "status": "running", "completedModules": completed})

    repo_paths = _list_repo_paths(ws)
    meta_docs = _list_meta_docs(ws)

    for module_id in to_run:
        if should_cancel():
            return _cancel_run(ws, run_id, app_version=app_version, completed=completed, on_event=on_event)

        skill = _module_skill(ws, module_id)
        role = _role_for(module_id)
        if not skill.exists():
            err = f"SKILL.md not found for module {module_id}"
            append_eventlog(ws, module_id, "ERROR", err)
            append_eventlog(ws, "run", "ERROR", f"runId={run_id} failed at {module_id}: {err}")
            on_event("module", {"module": module_id, "status": "ERROR", "summary": err})
            save_run_state(ws, run_id, status="failed", failedModule=module_id, error=err)
            on_event("run", {"runId": run_id, "status": "failed", "completedModules": completed})
            return {"runId": run_id, "status": "failed", "failedModule": module_id, "error": err,
                    "completedModules": completed}

        on_event("module", {"module": module_id, "status": "START"})
        append_eventlog(ws, module_id, "START", f"modules={version} app=v{app_version}")

        if module_id == WORKSPACE_INDEX_MODULE:
            summary, error = await _run_workspace_index(
                ws=ws,
                run_id=run_id,
                client=client,
                models=models,
                on_event=on_event,
                should_cancel=should_cancel,
                force_reindex=force_reindex,
            )
        else:
            model = models.get(module_id) or models.get("module") or models["module"]
            summary, error = await _run_module_with_retry(
                ws=ws, skill=skill, model=model, client=client, role=role, module_id=module_id,
                run_id=run_id, repo_paths=repo_paths, meta_docs=meta_docs,
                project_type=project_type, specific_instructions=specific_instructions,
                on_event=on_event, should_cancel=should_cancel,
                resume=is_resuming,
            )

        if should_cancel() or error == "cancelled":
            return _cancel_run(ws, run_id, app_version=app_version, completed=completed, on_event=on_event)

        if error is not None:
            append_eventlog(ws, module_id, "ERROR", error)
            append_eventlog(ws, "run", "ERROR", f"runId={run_id} failed at {module_id}: {error}")
            on_event("module", {"module": module_id, "status": "ERROR", "summary": error})
            save_run_state(ws, run_id, status="failed", failedModule=module_id, error=error)
            on_event("run", {"runId": run_id, "status": "failed", "completedModules": completed})
            return {"runId": run_id, "status": "failed", "failedModule": module_id, "error": error,
                    "completedModules": completed}

        completed.append(module_id)
        save_run_state(ws, run_id, completedModules=completed, status="running")
        on_event("module", {"module": module_id, "status": "SUCCESS", "summary": summary[:200]})
        on_event("run", {"runId": run_id, "status": "running", "completedModules": completed})

    save_run_state(ws, run_id, status="complete", completedModules=completed)
    append_eventlog(ws, "run", "SUCCESS", f"runId={run_id} completed {len(completed)} modules")
    _write_run_summary(ws, run_id, completed, project_type, version)
    on_event("run", {"runId": run_id, "status": "complete", "completedModules": completed})
    return {"runId": run_id, "status": "complete", "completedModules": completed}


# --- orchestration --------------------------------------------------------
async def run_modules(
    *,
    workspace: str,
    client: OpenRouterClient,
    models: dict[str, str],
    run_id: str,
    modules: list[str],
    project_type: str = "both",
    resume: bool = True,
    force: bool = False,
    force_reindex: bool = False,
    specific_instructions: str | None = None,
    on_event: EventCallback | None = None,
    should_cancel: CancelCheck | None = None,
    app_version: str = "unknown",
) -> dict[str, Any]:
    ws = Path(workspace)
    should_cancel = should_cancel or (lambda: False)
    on_event = on_event or (lambda ev, data: None)

    try:
        return await _run_modules_body(
            ws=ws,
            client=client,
            models=models,
            run_id=run_id,
            modules=modules,
            project_type=project_type,
            resume=resume,
            force=force,
            force_reindex=force_reindex,
            specific_instructions=specific_instructions,
            on_event=on_event,
            should_cancel=should_cancel,
            app_version=app_version,
        )
    except asyncio.CancelledError:
        completed = load_run_state(ws, run_id).get("completedModules", [])
        save_run_state(ws, run_id, status="cancelled")
        append_eventlog(ws, "run", "CANCELLED", f"runId={run_id} app=v{app_version} (task cancelled)")
        on_event("run", {"runId": run_id, "status": "cancelled", "completedModules": completed})
        raise
    except Exception as exc:
        completed = load_run_state(ws, run_id).get("completedModules", [])
        err = format_exception(exc)
        save_run_state(ws, run_id, status="failed", error=err)
        append_eventlog(ws, "run", "ERROR", f"runId={run_id} unhandled: {err}")
        on_event("run", {"runId": run_id, "status": "failed", "completedModules": completed, "error": err})
        raise


async def _run_workspace_index(
    *,
    ws: Path,
    run_id: str,
    client: OpenRouterClient,
    models: dict[str, str],
    on_event: EventCallback,
    should_cancel: CancelCheck,
    force_reindex: bool = False,
) -> tuple[str, str | None]:
    if should_cancel():
        return "", "cancelled"
    try:
        result = await agentic_reindex(
            workspace=ws,
            run_id=run_id,
            client=client,
            models=models,
            on_event=on_event,
            should_cancel=should_cancel,
            force_reindex=force_reindex,
        )
        if should_cancel():
            return "", "cancelled"
        count = result.get("itemCount", 0)
        enriched = result.get("enriched", 0)
        skipped = result.get("skipped", 0)
        return f"Indexed {count} items ({enriched} enriched, {skipped} skipped)", None
    except asyncio.CancelledError:
        return "", "cancelled"
    except Exception as exc:  # noqa: BLE001
        return "", format_exception(exc)


def _is_stuck_or_incomplete(exc: BaseException) -> bool:
    msg = str(exc).rstrip()
    return msg.endswith("(stuck)") or msg.endswith("(incomplete)")


async def _run_module_with_retry(*, ws, skill, model, client, role, module_id, run_id,
                                  repo_paths, meta_docs, project_type, specific_instructions,
                                  on_event, should_cancel, resume: bool = False) -> tuple[str, str | None]:
    # When resuming, if a prior run already produced both required artifacts,
    # treat the module as done instead of re-running it from scratch.
    out_dir = ws / "runs" / run_id / module_id
    if resume and (out_dir / "analysis.md").is_file() and (out_dir / "tasks.json").is_file():
        clear_agent_state(ws, run_id, module_id)
        append_eventlog(ws, module_id, "SKIP",
                        "analysis.md and tasks.json already present — not re-running")
        return f"{role} already complete (artifacts present)", None

    attempt = 0
    # First attempt resumes the saved transcript when resuming a failed/cancelled
    # run, so already-run turns and file reads are not repeated.
    resume_checkpoint = resume
    while True:
        try:
            summary = await run_agent(
                workspace=ws, instruction_file=skill, model=model, client=client, role=role,
                module_id=module_id, run_id=run_id, repo_paths=repo_paths, meta_docs=meta_docs,
                project_type=project_type, specific_instructions=specific_instructions,
                on_event=on_event,
                max_turns=get_max_turns(ws, module_id, resume=resume_checkpoint),
                resume_checkpoint=resume_checkpoint,
            )
            if summary.rstrip().endswith(("(incomplete)", "(stuck)")):
                raise RuntimeError(summary)
            if not (out_dir / "analysis.md").is_file() or not (out_dir / "tasks.json").is_file():
                raise RuntimeError(
                    f"{role} finished without analysis.md and tasks.json in runs/{run_id}/{module_id}/"
                )
            return summary, None
        except asyncio.CancelledError:
            return "", "cancelled"
        except Exception as exc:  # noqa: BLE001
            if should_cancel():
                return "", "cancelled"
            stuck = _is_stuck_or_incomplete(exc)
            if attempt < MAX_MODULE_RETRIES and (_is_transient(exc) or stuck):
                delay = min(RETRY_BASE_DELAY_SEC * (2 ** attempt), RETRY_MAX_DELAY_SEC)
                attempt += 1
                # A stuck/incomplete loop won't recover by replaying the same
                # transcript — restart this module fresh. Transient failures keep
                # their progress and resume from the checkpoint.
                if stuck:
                    clear_agent_state(ws, run_id, module_id)
                    resume_checkpoint = False
                else:
                    resume_checkpoint = True
                append_eventlog(ws, module_id, "RETRY",
                                f"attempt {attempt}/{MAX_MODULE_RETRIES} after {delay:.0f}s "
                                f"({'fresh' if stuck else 'resume'}): {exc}")
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    return "", "cancelled"
                continue
            return "", format_exception(exc)


def _write_run_summary(ws: Path, run_id: str, completed: list[str], project_type: str,
                       version: str) -> None:
    lines = [
        f"# Run Summary — {run_id}", "",
        f"- Project type: {project_type}",
        f"- Modules template version: modules={version}",
        f"- Completed modules: {len(completed)}", "",
        "## Per-module results", "",
    ]
    for module_id in completed:
        lines.append(f"### {module_id}")
        skips = read_skips(ws, run_id, module_id)
        skip_note = f" Tool skips: {len(skips)}." if skips else ""
        lines.append(
            f"- Status: SUCCESS. See `runs/{run_id}/{module_id}/analysis.md`.{skip_note}"
        )
        if skips:
            for sk in skips[:10]:
                lines.append(
                    f"  - skipped `{sk.get('name', '?')}` after {sk.get('attempts', '?')} attempts"
                )
        lines.append("")
    try:
        (_run_dir(ws, run_id) / "run-summary.md").write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass


def list_runs(workspace: Path) -> list[dict[str, Any]]:
    runs_dir = Path(workspace) / "runs"
    if not runs_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for d in sorted((p for p in runs_dir.iterdir() if p.is_dir()), reverse=True):
        out.append(load_run_state(workspace, d.name))
    return out
