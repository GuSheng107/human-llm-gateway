"""M14 工作台：把 normalized_request_json 投影为对话消息列表。

投影只把 normalized.context 作为唯一正文来源，instructions / system_blocks
作为技术补充，避免 Chat/Anthropic 的 messages 与 context 或 Responses 的
input 被重复展示。推理路径从不使用本投影，原始请求仍在 raw_payload_json
中完整保存。
"""

from __future__ import annotations

import json
import re
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
    original_type = str(block.get("type") or "text")
    # OpenAI Chat/Responses 与 Anthropic 的图片块统一成前端可直接渲染的
    # image；其他未知块保留原类型，方便工作台诊断。
    btype = "image" if original_type in {"image_url", "input_image"} else original_type
    if original_type in {"input_text", "output_text"}:
        btype = "text"
    item: dict[str, Any] = {"type": btype, "display_kind": "content"}
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
    source_type = block.get("source_type")
    if isinstance(source_type, str) and source_type:
        item["source_type"] = source_type
    elif original_type in {"image_url", "input_image"}:
        item["source_type"] = original_type
    elif isinstance(source, dict) and isinstance(source.get("type"), str):
        item["source_type"] = source["type"]
    if btype == "image" or (isinstance(source, dict) and source.get("type") == "base64"):
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
    for key in ("id", "call_id", "tool_call_id", "tool_use_id"):
        value = block.get(key)
        if isinstance(value, str) and value:
            item["tool_call_id"] = value
            break
    return item


def _text_blocks(text: str, *, role: str) -> list[dict[str, Any]]:
    """把用户正文与 Agent 技术包裹分开，技术块默认折叠。"""
    if not text:
        return []
    if role == "system":
        return [{"type": "text", "text": text, "display_kind": "technical"}]
    match = re.search(r"<user_input>(.*?)</user_input>", text, flags=re.DOTALL)
    if match is None:
        return [{"type": "text", "text": text, "display_kind": "content"}]
    blocks: list[dict[str, Any]] = []
    technical_before = text[: match.start()].strip()
    user_text = match.group(1)
    technical_after = text[match.end() :].strip()
    if technical_before:
        blocks.append({"type": "text", "text": technical_before, "display_kind": "technical"})
    if user_text:
        blocks.append({"type": "text", "text": user_text, "display_kind": "content"})
    if technical_after:
        blocks.append({"type": "text", "text": technical_after, "display_kind": "technical"})
    return blocks


def _blocks_from_content(content: Any, *, role: str) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return _text_blocks(content, role=role)
    if isinstance(content, list):
        blocks: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if isinstance(block.get("text"), str) and block.get("type") in {
                "text",
                "input_text",
                "output_text",
            }:
                blocks.extend(_text_blocks(block["text"], role=role))
            else:
                blocks.append(_block_from_mapping(block))
        return blocks
    return []


def project_messages(normalized: dict[str, Any]) -> list[dict[str, Any]]:
    """把 normalized request 投影为消息列表（含 preview / has_more）。"""
    messages: list[dict[str, Any]] = []

    instructions = normalized.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        messages.append(
            {
                "role": "system",
                "blocks": [{"type": "text", "text": instructions, "display_kind": "technical"}],
            }
        )
    system_blocks = normalized.get("system_blocks")
    if isinstance(system_blocks, list):
        for block in system_blocks:
            if isinstance(block, dict):
                projected = _block_from_mapping(block)
                projected["display_kind"] = "technical"
                messages.append({"role": "system", "blocks": [projected]})

    def add(
        role: str,
        content: Any,
        extra: dict[str, Any] | None = None,
        context_index: int | None = None,
    ) -> None:
        blocks = _blocks_from_content(content, role=role)
        if not blocks and extra:
            projected = _block_from_mapping(extra)
            projected["display_kind"] = "technical" if role == "system" else "content"
            blocks = [projected]
        if not blocks:
            return
        messages.append({"role": role, "blocks": blocks, "context_index": context_index})

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
