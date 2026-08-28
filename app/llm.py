import json
from typing import Any

import httpx
from cryptography.fernet import InvalidToken

from .dsl import ParsedEvent
from .enums import EventKind
from .models import LLMProvider
from .security import decrypt_secret


class LLMError(RuntimeError):
    pass


class LLMAdapter:
    @staticmethod
    def _endpoint(base_url: str, suffix: str) -> str:
        base = base_url.rstrip("/")
        if base.endswith(suffix.lstrip("/")):
            return base
        return base + "/" + suffix.lstrip("/")

    @staticmethod
    def _headers(provider: LLMProvider, api_key: str) -> dict[str, str]:
        if provider.protocol == "anthropic":
            return (
                {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
                if api_key
                else {"anthropic-version": "2023-06-01"}
            )
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def list_models(self, provider: LLMProvider, app_secret: str) -> list[dict[str, Any]]:
        if provider.base_url.startswith("mock://"):
            return [{"id": "mock-model", "object": "model", "owned_by": "mock"}]
        try:
            api_key = (
                decrypt_secret(provider.api_key_encrypted, app_secret)
                if provider.api_key_encrypted
                else ""
            )
            url = self._endpoint(provider.base_url, "models")
            headers = self._headers(provider, api_key)
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
            body = response.json()
            raw_models = body.get("data", body if isinstance(body, list) else [])
            return [item for item in raw_models if isinstance(item, dict) and item.get("id")]
        except (
            httpx.HTTPError,
            InvalidToken,
            KeyError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            raise LLMError("上游模型列表请求失败") from exc

    async def complete(
        self, provider: LLMProvider, model: str, messages: list[dict[str, Any]], app_secret: str
    ) -> list[ParsedEvent]:
        if provider.base_url.startswith("mock://"):
            return [ParsedEvent(EventKind.FINAL, "这是一个 mock LLM 回复。")]
        try:
            api_key = decrypt_secret(provider.api_key_encrypted, app_secret)
            options = json.loads(provider.options_json or "{}")
            # Provider options are still configurable, but the route-owned
            # model and normalized messages are authoritative and cannot be
            # overridden by stored extras.
            timeout = float(options.get("timeout_seconds", 60))
            request_options = {
                key: value for key, value in options.items() if key != "timeout_seconds"
            }
            payload = {**request_options, "model": model, "messages": messages, "stream": False}
            if provider.protocol == "anthropic":
                payload.pop("stream", None)
                payload.setdefault("max_tokens", int(options.get("max_tokens", 4096)))
                url = self._endpoint(provider.base_url, "messages")
            else:
                payload["stream"] = False
                url = self._endpoint(provider.base_url, "chat/completions")
            headers = self._headers(provider, api_key)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
            body = response.json()
            if provider.protocol == "anthropic":
                blocks = body.get("content", [])
                content = "".join(
                    str(item.get("text", ""))
                    for item in blocks
                    if isinstance(item, dict) and item.get("type") == "text"
                )
                reasoning = "".join(
                    str(item.get("thinking", ""))
                    for item in blocks
                    if isinstance(item, dict) and item.get("type") == "thinking"
                )
            else:
                message = body["choices"][0]["message"]
                content = message.get("content", "")
                if isinstance(content, list):
                    content = "".join(
                        str(item.get("text", "")) for item in content if isinstance(item, dict)
                    )
                reasoning = message.get("reasoning_content", message.get("thinking", ""))
            events: list[ParsedEvent] = []
            if reasoning:
                events.append(ParsedEvent(EventKind.REASONING, reasoning))
            events.append(ParsedEvent(EventKind.FINAL, content))
            return events
        except (
            httpx.HTTPError,
            InvalidToken,
            KeyError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            raise LLMError("上游 LLM 请求失败") from exc
