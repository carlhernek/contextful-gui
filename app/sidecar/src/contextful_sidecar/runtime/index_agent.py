"""Per-item bounded agent for workspace indexing."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from contextful_sidecar.runtime.activity import append_activity
from contextful_sidecar.runtime.eventlog import append_eventlog
from contextful_sidecar.runtime.indexing import (
    _heuristic_description,
    _heuristic_keywords,
    _parse_enrichment,
)
from contextful_sidecar.runtime.openrouter import OpenRouterClient
from contextful_sidecar.runtime.tool_runner import run_tool_with_liveness
from contextful_sidecar.runtime.tools import INDEX_TOOL_DEFINITIONS, execute_tool

EventCallback = Callable[[str, Any], None]
CancelCheck = Callable[[], bool]

MODULE_ID = "workspace-index"
DEFAULT_MAX_TURNS = 6


def _index_system_prompt(item: dict[str, Any]) -> str:
    return (
        "You are a workspace indexer. Analyze ONE item and produce a concise index entry.\n"
        f"Item id: {item['id']}\n"
        f"Type: {item['type']}\n"
        f"Path: {item['path']}\n\n"
        "Use gather_context first for repos and large trees — it surfaces README, docs, "
        "API specs, and stack manifests. If docs/specs sufficiently describe the item, "
        "summarize from those and stop; do not read the whole repo.\n"
        "You may also use read_file, list_directory, and grep_repo.\n"
        "When done, respond with ONLY valid JSON:\n"
        '{"description": "one sentence max 120 chars", "keywords": ["token1", "token2"]}\n'
        "keywords: 3-8 lowercase tokens."
    )


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

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _index_system_prompt(item)},
        {
            "role": "user",
            "content": f"Index this item now. Path: {item['path']}",
        },
    ]

    turn = 0

    def on_token(tok: str) -> None:
        on_event("token", tok)

    while turn < max_turns:
        if should_cancel():
            break
        turn += 1
        on_event("turn", {"module": MODULE_ID, "turn": turn, "maxTurns": max_turns, **event_extra})
        append_eventlog(workspace, MODULE_ID, "TURN", f"{item_id} turn {turn}/{max_turns}")
        append_activity(
            workspace,
            run_id,
            MODULE_ID,
            "turn",
            turn=turn,
            maxTurns=max_turns,
            **event_extra,
        )

        response = await client.chat_completion(
            model=model,
            messages=messages,
            tools=INDEX_TOOL_DEFINITIONS,
            on_token=on_token,
        )
        message = response["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
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
            parsed = _parse_enrichment(content)
            if not parsed.get("description"):
                parsed["description"] = _heuristic_description(
                    item["type"], item.get("name", ""), item.get("snippet") or ""
                )
            if not parsed.get("keywords"):
                parsed["keywords"] = _heuristic_keywords(
                    item["type"], item.get("name", ""), item["path"]
                )
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
            append_eventlog(
                workspace,
                MODULE_ID,
                "TOOL",
                f"{item_id} {name} {json.dumps(args)[:160]}",
            )
            on_event("tool", {"name": name, "args": args, **event_extra})
            append_activity(
                workspace,
                run_id,
                MODULE_ID,
                "tool",
                turn=turn,
                name=name,
                args=args,
                **event_extra,
            )
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
                tool_executor=execute_tool,
            )
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id") or name,
                "content": result,
            })

    err = f"index agent stopped after {max_turns} turns for {item_id}"
    append_activity(workspace, run_id, MODULE_ID, "error", text=err, **event_extra)
    return {
        "description": _heuristic_description(
            item["type"], item.get("name", ""), item.get("snippet") or ""
        ),
        "keywords": _heuristic_keywords(item["type"], item.get("name", ""), item["path"]),
    }
