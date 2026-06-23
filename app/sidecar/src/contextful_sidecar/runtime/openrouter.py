"""Async OpenRouter client with streaming chat completions."""
from __future__ import annotations

import asyncio
import json
import random
from typing import Any, Callable

import certifi
import httpx

from contextful_sidecar.runtime.transient import is_transient_exception

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
LLM_MAX_RETRIES = 2
LLM_RETRY_BASE_DELAY_SEC = 2.0
LLM_RETRY_MAX_DELAY_SEC = 20.0
_TRANSIENT_HTTP = frozenset({429, 502, 503, 504})


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

        last_exc: BaseException | None = None
        for attempt in range(LLM_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(verify=certifi.where(), timeout=300) as c:
                    if not body["stream"]:
                        r = await c.post(
                            f"{OPENROUTER_BASE}/chat/completions", headers=self.headers, json=body
                        )
                        if r.status_code in _TRANSIENT_HTTP and attempt < LLM_MAX_RETRIES:
                            await self._backoff(attempt)
                            continue
                        r.raise_for_status()
                        return r.json()
                    return await self._stream(c, body, on_token)
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code in _TRANSIENT_HTTP and attempt < LLM_MAX_RETRIES:
                    await self._backoff(attempt)
                    continue
                raise
            except (httpx.TransportError, httpx.ReadTimeout, asyncio.TimeoutError) as exc:
                last_exc = exc
                if attempt < LLM_MAX_RETRIES and is_transient_exception(exc):
                    await self._backoff(attempt)
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("chat_completion failed without response")

    async def _backoff(self, attempt: int) -> None:
        delay = min(
            LLM_RETRY_BASE_DELAY_SEC * (2 ** attempt) + random.uniform(0, 0.5),
            LLM_RETRY_MAX_DELAY_SEC,
        )
        await asyncio.sleep(delay)

    async def _stream(self, client, body, on_token) -> dict[str, Any]:
        content = ""
        tool_calls: dict[int, dict] = {}
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
