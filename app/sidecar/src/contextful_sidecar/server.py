"""NDJSON request/response loop over stdin/stdout."""
from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from typing import Any

from contextful_sidecar.runtime.chat import handle_chat
from contextful_sidecar.runtime.openrouter import OpenRouterClient
from contextful_sidecar.runtime.preview import preview_file
from contextful_sidecar.runtime.runs import load_run_state, run_modules

DEFAULT_MODELS: dict[str, str] = {
    "orchestrator": "deepseek/deepseek-v4-flash",
    "module": "deepseek/deepseek-v4-flash",
}


def _write_json(payload: dict[str, Any]) -> None:
    try:
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()
    except (OSError, BrokenPipeError, ValueError):
        # stdout closed (parent quit) -> exit cleanly instead of crashing.
        raise SystemExit(0) from None


class SidecarServer:
    def __init__(self) -> None:
        self.api_key = ""
        self.models: dict[str, str] = dict(DEFAULT_MODELS)
        self.client: OpenRouterClient | None = None
        self._cancel_requested = False
        self._active_task: asyncio.Task[dict[str, Any]] | None = None

    # --- cancellation -----------------------------------------------------
    def request_cancel(self) -> None:
        self._cancel_requested = True
        if self._active_task and not self._active_task.done():
            self._active_task.cancel()

    def should_cancel(self) -> bool:
        return self._cancel_requested

    def clear_cancel(self) -> None:
        self._cancel_requested = False

    # --- events -----------------------------------------------------------
    def _emit_event(self, req_id: str | None, event: str, data: Any) -> None:
        # No-op once cancelled -> prevents post-cancel event noise in the UI.
        if self.should_cancel():
            return
        _write_json({"id": req_id, "event": event, "data": data})

    # --- configuration ----------------------------------------------------
    def configure(self, api_key: str, models: dict[str, str] | None) -> None:
        self.api_key = api_key or ""
        self.models = dict(DEFAULT_MODELS)
        if models:
            self.models.update({k: v for k, v in models.items() if v})
        self.client = OpenRouterClient(self.api_key) if self.api_key else None

    # --- dispatch ---------------------------------------------------------
    async def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params") or {}
        if method == "cancel":
            self.request_cancel()
            return {"id": req_id, "result": {"ok": True}}
        self.clear_cancel()
        try:
            if method == "configure":
                self.configure(params.get("api_key", ""), params.get("models"))
                return {"id": req_id, "result": {"ok": True}}
            if method == "health":
                if not self.client:
                    return {"id": req_id, "result": {"ok": False, "error": "not configured"}}
                return {"id": req_id, "result": {"ok": await self.client.health()}}
            if method == "list_models":
                if not self.client:
                    return {"id": req_id, "error": "not configured"}
                return {"id": req_id, "result": {"models": await self.client.list_models()}}
            if not self.client:
                return {"id": req_id, "error": "not configured"}
            workspace = params.get("workspace", "")
            if method == "run_modules":
                summary = await run_modules(
                    workspace=workspace, client=self.client, models=self.models,
                    run_id=params["runId"], modules=params["modules"],
                    project_type=params.get("projectType", "both"),
                    resume=params.get("resume", True), force=params.get("force", False),
                    specific_instructions=params.get("specific_instructions"),
                    on_event=lambda ev, data: self._emit_event(req_id, ev, data),
                    should_cancel=self.should_cancel,
                )
                if self.should_cancel():
                    return {"id": req_id, "error": "cancelled"}
                return {"id": req_id, "result": summary}
            if method == "get_run_state":
                return {"id": req_id, "result": load_run_state(Path(workspace), params["runId"])}
            if method == "chat":
                reply = await handle_chat(
                    workspace=workspace, message=params.get("message", ""),
                    client=self.client, models=self.models,
                    on_event=lambda ev, data: self._emit_event(req_id, ev, data),
                    should_cancel=self.should_cancel,
                )
                return {"id": req_id, "result": reply}
            if method == "preview":
                return {"id": req_id, "result": preview_file(
                    Path(workspace), params.get("path", ""), params.get("base", "repos"))}
            return {"id": req_id, "error": f"unknown method: {method}"}
        except asyncio.CancelledError:
            return {"id": req_id, "error": "cancelled"}
        except Exception as exc:  # noqa: BLE001
            return {"id": req_id, "error": str(exc)}


async def _run_server_async(server: SidecarServer) -> None:
    loop = asyncio.get_event_loop()
    stdin_queue: asyncio.Queue[str | None] = asyncio.Queue()

    def feeder() -> None:
        try:
            for line in sys.stdin:
                asyncio.run_coroutine_threadsafe(stdin_queue.put(line), loop)
        except (OSError, ValueError):
            pass
        finally:
            asyncio.run_coroutine_threadsafe(stdin_queue.put(None), loop)

    threading.Thread(target=feeder, daemon=True).start()
    active: asyncio.Task[dict[str, Any]] | None = None

    while True:
        if active is not None and not active.done():
            line_task = asyncio.create_task(stdin_queue.get())
            done, pending = await asyncio.wait(
                {active, line_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()

            if line_task in done:
                line = line_task.result()
                if line is None:
                    # EOF. If the active request finished in the SAME tick,
                    # flush its response before exiting; else cancel it.
                    if active in done:
                        try:
                            _write_json(active.result())
                        except asyncio.CancelledError:
                            _write_json({"id": None, "error": "cancelled"})
                        server._active_task = None
                        active = None
                    elif active is not None and not active.done():
                        active.cancel()
                    break
                line = line.strip()
                if line:
                    request = json.loads(line)
                    if request.get("method") == "cancel":
                        server.request_cancel()
                        if active is not None and not active.done():
                            active.cancel()
                        _write_json({"id": request.get("id"), "result": {"ok": True}})
                        continue

            if active in done:
                try:
                    response = active.result()
                except asyncio.CancelledError:
                    response = {"id": None, "error": "cancelled"}
                _write_json(response)
                server._active_task = None
                active = None
            continue

        line = await stdin_queue.get()
        if line is None:
            break
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            _write_json({"error": f"invalid json: {exc}"})
            continue
        if request.get("method") == "cancel":
            server.request_cancel()
            _write_json({"id": request.get("id"), "result": {"ok": True}})
            continue
        server.clear_cancel()
        active = asyncio.create_task(server.handle(request))
        server._active_task = active


def run_server() -> None:
    server = SidecarServer()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run_server_async(server))
    except (OSError, BrokenPipeError, SystemExit):
        raise SystemExit(0) from None
