"""Per-item bounded agent for workspace indexing."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from contextful_sidecar.runtime.activity import append_activity
from contextful_sidecar.runtime.indexing import (
    _heuristic_description,
    _heuristic_keywords,
    _parse_enrichment,
)
from contextful_sidecar.runtime.openrouter import OpenRouterClient
from contextful_sidecar.runtime.step_log import log_step, logged_chat_completion
from contextful_sidecar.runtime.tool_runner import run_tool_with_liveness
from contextful_sidecar.runtime.tools import INDEX_TOOL_DEFINITIONS, _gather_context, execute_tool

EventCallback = Callable[[str, Any], None]
CancelCheck = Callable[[], bool]

MODULE_ID = "workspace-index"
DEFAULT_MAX_TURNS = 8


def _index_system_prompt(item: dict[str, Any]) -> str:
    return (
        "You are a workspace indexer. Analyze ONE item and produce a concise index entry.\n"
        f"Item id: {item['id']}\n"
        f"Type: {item['type']}\n"
        f"Path: {item['path']}\n\n"
        "Use gather_context first for repos — it surfaces README, docs, API specs, and stack manifests. "
        "For meta documents, read_file extracts text from .docx files.\n"
        "If README, manifest, or document text already describes the item, respond with JSON immediately — "
        "do not keep exploring the whole tree.\n"
        "You may also use read_file, list_directory, and grep_repo (scoped to this item).\n"
        "When done, respond with ONLY valid JSON:\n"
        '{"description": "one sentence max 120 chars", "keywords": ["token1", "token2"]}\n'
        "keywords: 3-8 lowercase tokens."
    )


def _initial_user_message(item: dict[str, Any]) -> str:
    snippet = (item.get("snippet") or "").strip()
    parts = [
        f"Index this item now.",
        f"Path: {item['path']}",
        f"Name: {item.get('name', '')}",
        f"Type: {item['type']}",
    ]
    if snippet:
        parts.append(f"\nScan preview:\n{snippet[:2000]}")
    return "\n".join(parts)


def _tool_executor_for_item(
    workspace: Path,
    item: dict[str, Any],
) -> Callable[[Path, str, dict[str, Any]], str]:
    def _run(ws: Path, name: str, args: dict[str, Any]) -> str:
        if name == "grep_repo":
            args = dict(args)
            if not args.get("repo") and not args.get("path"):
                if item["type"] == "meta":
                    args["path"] = str(Path(item["path"]).parent)
                elif item["type"] == "repo":
                    args["repo"] = item.get("name") or Path(item["path"]).name
        return execute_tool(ws, name, args)

    return _run


def _result_from_content(
    item: dict[str, Any],
    content: str,
    *,
    source_if_parsed: str = "ai",
) -> dict[str, Any]:
    parsed = _parse_enrichment(content)
    has_json = bool(parsed.get("description") or parsed.get("keywords"))
    if not parsed.get("description"):
        parsed["description"] = _heuristic_description(
            item["type"], item.get("name", ""), item.get("snippet") or ""
        )
    if not parsed.get("keywords"):
        parsed["keywords"] = _heuristic_keywords(
            item["type"], item.get("name", ""), item["path"]
        )
    parsed["source"] = source_if_parsed if has_json else "heuristic"
    return parsed


async def index_item(
    *,
    workspace: Path,
    run_id: str,
    item: dict[str, Any],
    item_index: int,
    item_total: int,
    model: str,
    client: OpenRouterClient,
    on_event: EventCallback | None = None,
    should_cancel: CancelCheck | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
) -> dict[str, Any]:
    workspace = Path(workspace)
    on_event = on_event or (lambda _e, _d: None)
    should_cancel = should_cancel or (lambda: False)
    item_id = item["id"]
    event_extra = {"itemId": item_id, "itemIndex": item_index, "itemTotal": item_total}
    tool_executor = _tool_executor_for_item(workspace, item)

    append_activity(
        workspace,
        run_id,
        MODULE_ID,
        "item",
        status="indexing",
        itemId=item_id,
        itemIndex=item_index,
        itemTotal=item_total,
        path=item.get("path"),
    )

    seed_parts = [_initial_user_message(item)]
    if item["type"] == "repo":
        try:
            ctx = _gather_context(workspace, item["path"])
            if ctx and not ctx.startswith("ERROR"):
                seed_parts.append(f"\nInitial context bundle:\n{ctx[:8000]}")
        except Exception:
            pass

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _index_system_prompt(item)},
        {"role": "user", "content": "\n".join(seed_parts)},
    ]

    turn = 0
    gather_done = item["type"] == "repo"

    def on_token(tok: str) -> None:
        on_event("token", tok)

    while turn < max_turns:
        if should_cancel():
            break
        turn += 1
        synthesis_only = turn >= max_turns
        on_event("turn", {"module": MODULE_ID, "turn": turn, "maxTurns": max_turns, **event_extra})
        log_step(
            workspace,
            scope=MODULE_ID,
            status="TURN",
            message=f"{item_id} turn {turn}/{max_turns}",
            run_id=run_id,
            module_id=MODULE_ID,
            activity_kind="turn",
            turn=turn,
            maxTurns=max_turns,
            **event_extra,
        )

        if gather_done and turn >= max_turns - 1:
            messages.append({
                "role": "system",
                "content": "You have enough context. Respond with ONLY valid JSON now — no more tools.",
            })

        response = await logged_chat_completion(
            workspace=workspace,
            run_id=run_id,
            module_id=MODULE_ID,
            scope=MODULE_ID,
            client=client,
            model=model,
            messages=messages,
            tools=None if synthesis_only else INDEX_TOOL_DEFINITIONS,
            on_token=on_token,
            event_extra=event_extra,
        )
        message = response["choices"][0]["message"]
        tool_calls = [] if synthesis_only else (message.get("tool_calls") or [])
        messages.append(message)

        content = message.get("content") or ""
        if content.strip():
            append_activity(
                workspace,
                run_id,
                MODULE_ID,
                "thinking",
                turn=turn,
                text=content,
                **event_extra,
            )

        if not tool_calls:
            parsed = _result_from_content(item, content)
            append_activity(
                workspace,
                run_id,
                MODULE_ID,
                "final",
                turn=turn,
                text=content,
                description=parsed.get("description"),
                keywords=parsed.get("keywords"),
                **event_extra,
            )
            return parsed

        on_event("token", "\n\n")
        for call in tool_calls:
            fn = call.get("function") or {}
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if name == "gather_context":
                gather_done = True
            log_step(
                workspace,
                scope=MODULE_ID,
                status="TOOL",
                message=f"{item_id} {name} {json.dumps(args)[:160]}",
                run_id=run_id,
                module_id=MODULE_ID,
                activity_kind="tool",
                turn=turn,
                name=name,
                args=args,
                **event_extra,
            )
            on_event("tool", {"name": name, "args": args, **event_extra})
            result = await run_tool_with_liveness(
                workspace=workspace,
                log_scope=MODULE_ID,
                turn=turn,
                name=name,
                args=args,
                on_event=on_event,
                should_cancel=should_cancel,
                run_id=run_id,
                module_id=MODULE_ID,
                event_extra=event_extra,
                tool_executor=tool_executor,
            )
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id") or name,
                "content": result,
            })

        if gather_done and turn >= 3:
            messages.append({
                "role": "system",
                "content": "You likely have enough context — output JSON on your next response.",
            })

    warn = f"index agent stopped after {max_turns} turns for {item_id}"
    log_step(
        workspace,
        scope=MODULE_ID,
        status="WARN",
        message=warn,
        run_id=run_id,
        module_id=MODULE_ID,
        activity_kind="error",
        **event_extra,
    )
    return {
        "description": _heuristic_description(
            item["type"], item.get("name", ""), item.get("snippet") or ""
        ),
        "keywords": _heuristic_keywords(item["type"], item.get("name", ""), item["path"]),
        "source": "heuristic",
    }
