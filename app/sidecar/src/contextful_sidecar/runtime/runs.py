"""Run orchestration: sequential modules, retry/backoff, resumable state (spec section 9)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from contextful_sidecar.runtime.agent import run_agent
from contextful_sidecar.runtime.eventlog import append_eventlog
from contextful_sidecar.runtime.openrouter import OpenRouterClient

EventCallback = Callable[[str, Any], None]
CancelCheck = Callable[[], bool]

MAX_MODULE_RETRIES = 2          # 3 attempts total
RETRY_BASE_DELAY_SEC = 2.0      # exponential, capped at 15s
RETRY_MAX_DELAY_SEC = 15.0
_TRANSIENT_MARKERS = ("ssl", "certificate", "timeout", "timed out", "connection",
                      "network", "rate limit", "503", "502", "504", "429", "disconnect")

RUN_STATE_FILE = ".run-state.json"
TEMPLATE_VERSION_FILE = "modules/template-version.txt"


def _is_transient(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _TRANSIENT_MARKERS)


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
    state = load_run_state(workspace, run_id)
    state.update(updates)
    state["runId"] = run_id
    state["updatedAt"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
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
        return {"to_run": list(selected), "alreadyComplete": False}

    if resume and status in {"failed", "cancelled", "running", "complete"}:
        to_run = [m for m in selected if m not in completed]
        if not to_run:
            return {"to_run": [], "alreadyComplete": True}
        return {"to_run": to_run, "alreadyComplete": False}

    return {"to_run": list(selected), "alreadyComplete": False}


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
    specific_instructions: str | None = None,
    on_event: EventCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> dict[str, Any]:
    ws = Path(workspace)
    should_cancel = should_cancel or (lambda: False)
    on_event = on_event or (lambda ev, data: None)

    version = template_version(ws)
    plan = filter_modules(ws, run_id, modules, resume, force)
    to_run = plan["to_run"]
    if plan.get("alreadyComplete"):
        return {"runId": run_id, "status": "complete", "completedModules":
                load_run_state(ws, run_id).get("completedModules", []), "alreadyComplete": True}

    append_eventlog(ws, "run", "START", f"runId={run_id} modules={version} ({len(to_run)} to run)")
    state = save_run_state(ws, run_id, status="running", error=None, failedModule=None)
    completed = list(state.get("completedModules", []))
    on_event("run", {"runId": run_id, "status": "running", "completedModules": completed})

    repo_paths = _list_repo_paths(ws)
    meta_docs = _list_meta_docs(ws)

    for module_id in to_run:
        if should_cancel():
            save_run_state(ws, run_id, status="cancelled")
            append_eventlog(ws, "run", "CANCELLED", f"runId={run_id}")
            on_event("run", {"runId": run_id, "status": "cancelled", "completedModules": completed})
            return {"runId": run_id, "status": "cancelled", "completedModules": completed}

        skill = _module_skill(ws, module_id)
        role = _role_for(module_id)
        if not skill.exists():
            err = f"SKILL.md not found for module {module_id}"
            append_eventlog(ws, module_id, "ERROR", err)
            on_event("module", {"module": module_id, "status": "ERROR", "summary": err})
            save_run_state(ws, run_id, status="failed", failedModule=module_id, error=err)
            on_event("run", {"runId": run_id, "status": "failed", "completedModules": completed})
            return {"runId": run_id, "status": "failed", "failedModule": module_id, "error": err,
                    "completedModules": completed}

        on_event("module", {"module": module_id, "status": "START"})
        append_eventlog(ws, module_id, "START", f"modules={version}")
        model = models.get(module_id) or models.get("module") or models["module"]

        summary, error = await _run_module_with_retry(
            ws=ws, skill=skill, model=model, client=client, role=role, module_id=module_id,
            run_id=run_id, repo_paths=repo_paths, meta_docs=meta_docs,
            project_type=project_type, specific_instructions=specific_instructions,
            on_event=on_event, should_cancel=should_cancel,
        )

        if should_cancel():
            save_run_state(ws, run_id, status="cancelled")
            on_event("run", {"runId": run_id, "status": "cancelled", "completedModules": completed})
            return {"runId": run_id, "status": "cancelled", "completedModules": completed}

        if error is not None:
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


async def _run_module_with_retry(*, ws, skill, model, client, role, module_id, run_id,
                                  repo_paths, meta_docs, project_type, specific_instructions,
                                  on_event, should_cancel) -> tuple[str, str | None]:
    attempt = 0
    while True:
        try:
            summary = await run_agent(
                workspace=ws, instruction_file=skill, model=model, client=client, role=role,
                module_id=module_id, run_id=run_id, repo_paths=repo_paths, meta_docs=meta_docs,
                project_type=project_type, specific_instructions=specific_instructions,
                on_event=on_event,
            )
            # The agent returns a sentinel string on turn exhaustion; treat as transient error.
            if summary.endswith("(incomplete)"):
                raise RuntimeError(summary)
            return summary, None
        except asyncio.CancelledError:
            return "", "cancelled"
        except Exception as exc:  # noqa: BLE001
            if should_cancel():
                return "", "cancelled"
            if attempt < MAX_MODULE_RETRIES and _is_transient(exc):
                delay = min(RETRY_BASE_DELAY_SEC * (2 ** attempt), RETRY_MAX_DELAY_SEC)
                attempt += 1
                append_eventlog(ws, module_id, "RETRY",
                                f"attempt {attempt}/{MAX_MODULE_RETRIES} after {delay:.0f}s: {exc}")
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    return "", "cancelled"
                continue
            return "", str(exc)


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
        lines.append(f"- Status: SUCCESS. See `runs/{run_id}/{module_id}/analysis.md`.")
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
