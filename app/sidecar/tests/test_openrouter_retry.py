"""OpenRouter client retry behavior."""
from __future__ import annotations

import asyncio

import httpx

from contextful_sidecar.runtime.openrouter import OpenRouterClient


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.headers: dict = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)


_calls = {"n": 0}


class _FlakyClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, *args, **kwargs):
        _calls["n"] += 1
        if _calls["n"] < 3:
            return _FakeResponse(503)
        request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        )


def test_chat_completion_retries_transient_503(monkeypatch):
    _calls["n"] = 0
    monkeypatch.setattr(
        "contextful_sidecar.runtime.openrouter.httpx.AsyncClient",
        _FlakyClient,
    )
    async def _noop_sleep(_):
        return None
    monkeypatch.setattr("contextful_sidecar.runtime.openrouter.asyncio.sleep", _noop_sleep)
    client = OpenRouterClient("test-key")
    result = asyncio.run(client.chat_completion(
        model="fake",
        messages=[{"role": "user", "content": "hi"}],
        on_token=None,
    ))
    assert result["choices"][0]["message"]["content"] == "ok"
