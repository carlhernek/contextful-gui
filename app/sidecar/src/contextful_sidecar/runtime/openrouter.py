"""Async OpenRouter client with streaming chat completions."""
from __future__ import annotations

import json
from typing import Any, Callable

import certifi
import httpx

OPENROUTER_BASE = "https://openrouter.ai/api/v1"


class OpenRouterClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/contextful/contextful",
            "X-Title": "Contextful",
        }

    async def health(self) -> bool:
        async with httpx.AsyncClient(verify=certifi.where(), timeout=15) as c:
            r = await c.get(f"{OPENROUTER_BASE}/models", headers=self.headers)
            return r.status_code == 200

    async def list_models(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(verify=certifi.where(), timeout=30) as c:
            r = await c.get(f"{OPENROUTER_BASE}/models", headers=self.headers)
            r.raise_for_status()
            return r.json().get("data", [])

    async def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": on_token is not None,
        }
        if tools:
            body["tools"] = tools
        async with httpx.AsyncClient(verify=certifi.where(), timeout=300) as c:
            if not body["stream"]:
                r = await c.post(
                    f"{OPENROUTER_BASE}/chat/completions", headers=self.headers, json=body
                )
                r.raise_for_status()
                return r.json()
            return await self._stream(c, body, on_token)

    async def _stream(self, client, body, on_token) -> dict[str, Any]:
        content = ""
        tool_calls: dict[int, dict] = {}  # accumulate deltas BY INDEX
        async with client.stream(
            "POST", f"{OPENROUTER_BASE}/chat/completions", headers=self.headers, json=body
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                delta = json.loads(data)["choices"][0]["delta"]
                if tok := delta.get("content"):
                    content += tok
                    if on_token:
                        on_token(tok)
                for tc in delta.get("tool_calls") or []:
                    slot = tool_calls.setdefault(
                        tc["index"],
                        {"id": None, "type": "function",
                         "function": {"name": "", "arguments": ""}},
                    )
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]
        message: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            message["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
        finish_reason = "tool_calls" if tool_calls else "stop"
        return {"choices": [{"message": message, "finish_reason": finish_reason}]}
