"""M14 工作台：把 normalized_request_json 投影为对话消息列表。

投影不做协议语义转换，只做“展示”排版：把 messages / context / input /
system 拼接成带 index 的线性消息流，供工作台中间栏和消费。
推理路径从不使用本投影，原始请求仍在 raw_payload_json 中完整保存。
"""

from __future__ import annotations

import json
from typing import Any

PREVIEW_CHARS = 800


def _text_preview(text: str) -> tuple[str, bool]:
    if len(text) <= PREVIEW_CHARS:
        return text, False
    return text[:PREVIEW_CHARS], True


def _resolve_image_url(block: dict[str, Any]) -> str | None:
    """把各协议图片块归一为可直接渲染的 URL 字符串。

    - OpenAI Chat: {"image_url": "https://... "} 或 {"image_url": {"url": ...}}
    - Anthropic: {"source": {"type": "base64", "media_type": ..., "data": ...}}
    - Responses: {"content": [{"type": "input_image", "image_url": ...}]}
    """
    image_url = block.get("image_url")
    if isinstance(image_url, str) and image_url:
        return image_url
    if isinstance(image_url, dict):
        url = image_url.get("url")
        if isinstance(url, str) and url:
            return url
    source = block.get("source")
    if isinstance(source, dict):
        media_type = source.get("media_type") or source.get("content_type") or ""
        data = source.get("data")
        if source.get("type") == "base64" and isinstance(data, str) and data:
            return f"data:{media_type};base64,{data}"
    return None


def _block_from_mapping(block: dict[str, Any]) -> dict[str, Any]:
    btype = str(block.get("type") or "text")
    item: dict[str, Any] = {"type": btype}
    text = block.get("text")
    if isinstance(text, str):
        item["text"] = text
    name = block.get("name")
    if isinstance(name, str):
        item["name"] = name
    media = block.get("media_type") or block.get("mime_type")
    if isinstance(media, str):
        item["media_type"] = media
    source = block.get("source")
    if isinstance(source, dict):
        media2 = source.get("media_type") or source.get("content_type")
        if isinstance(media2, str):
            item.setdefault("media_type", media2)
    if btype in {"image", "input_image", "image_url"} or (
        isinstance(source, dict) and source.get("type") == "base64"
    ):
        url = _resolve_image_url(block)
        if url is not None:
            item["url"] = url
    size = block.get("size")
    if isinstance(size, dict):
        width = size.get("width")
        height = size.get("height")
        if isinstance(width, int):
            item["width"] = width
        if isinstance(height, int):
            item["height"] = height
    return item


def _blocks_from_content(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if isinstance(content, list):
        return [_block_from_mapping(b) for b in content if isinstance(b, dict)]
    return []


def project_messages(normalized: dict[str, Any]) -> list[dict[str, Any]]:
    """把 normalized request 投影为消息列表（含 preview / has_more）。"""
    messages: list[dict[str, Any]] = []

    instructions = normalized.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        messages.append(
            {
                "role": "system",
                "blocks": [{"type": "text", "text": instructions}],
            }
        )
    system_blocks = normalized.get("system_blocks")
    if isinstance(system_blocks, list):
        for block in system_blocks:
            if isinstance(block, dict):
                messages.append({"role": "system", "blocks": [_block_from_mapping(block)]})

    def add(
        role: str,
        content: Any,
        extra: dict[str, Any] | None = None,
        context_index: int | None = None,
    ) -> None:
        blocks = _blocks_from_content(content)
        if not blocks and extra:
            blocks = [extra]
        if not blocks:
            return
        messages.append({"role": role, "blocks": blocks, "context_index": context_index})

    raw_messages = normalized.get("messages")
    if isinstance(raw_messages, list):
        for message in raw_messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "user")
            add(role, message.get("content"), message if message.get("type") else None)

    context = normalized.get("context")
    if isinstance(context, list):
        for ctx_index, item in enumerate(context):
            if not isinstance(item, dict):
                continue
            if "role" in item:
                add(
                    str(item.get("role") or "user"),
                    item.get("content"),
                    item,
                    context_index=ctx_index,
                )
            elif item.get("type") == "function_call_output":
                add(
                    "tool",
                    item.get("output"),
                    {"type": "tool_result", "tool_call_id": item.get("call_id")},
                    context_index=ctx_index,
                )

    raw_input = normalized.get("input")
    if isinstance(raw_input, str) and raw_input.strip():
        add("user", raw_input, None)
    elif isinstance(raw_input, list):
        for item in raw_input:
            if not isinstance(item, dict):
                continue
            itype = item.get("type")
            if itype == "message":
                add(str(item.get("role") or "user"), item.get("content"), item)
            elif itype == "function_call_output":
                add(
                    "tool",
                    item.get("output"),
                    {"type": "tool_result", "tool_call_id": item.get("call_id")},
                )

    for index, message in enumerate(messages):
        blocks = message["blocks"]
        text = "\n".join(str(b.get("text") or "") for b in blocks if b.get("text"))
        preview, has_more = _text_preview(text)
        message["index"] = index
        message["preview"] = preview
        message["length"] = len(text)
        message["has_more"] = has_more
    return messages


def load_projected_task_messages(raw_json: str) -> list[dict[str, Any]]:
    normalized: dict[str, Any] = {}
    try:
        parsed = json.loads(raw_json or "{}")
        if isinstance(parsed, dict):
            normalized = parsed
    except (ValueError, TypeError):
        normalized = {}
    return project_messages(normalized)
