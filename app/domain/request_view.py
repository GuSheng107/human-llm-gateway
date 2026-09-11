"""RequestView：把当前任务的规范化请求投影为分区展示视图（§6.2）。

只做「本次请求」的展示投影，不制造任何持久化聊天语义：
- current_input：本次请求需要回答的最新输入。
- caller_system：调用方 system / developer 指令（默认折叠）。
- attached_context：随请求附带的早期消息与 previous_response_id 展开内容。
- attachments：图片/文件/音频等附件摘要（列表不携带 base64 正文）。

每个 ContextItem 与 ContentBlock 都有由协议路径稳定生成的不透明 ID
（ctx_... / blk_...）：同一 RequestTask 生命周期内重复读取保持一致，
用于按需加载（block 端点）与上下文排除（ctx ID），不暴露数组下标语义。
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from ..core.constants import MAX_EXPANDED_ITEMS

# 单个文本块的预览长度（超出部分经 block 端点按需加载）。
TEXT_PREVIEW_CHARS = 1200

# 可安全内联预览的栅格图片媒体类型；SVG/HTML/未知二进制只允许下载。
_PREVIEWABLE_MEDIA_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/avif"}
)

_SYSTEM_ROLES = frozenset({"system", "developer"})


def _opaque_id(prefix: str, seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:20]}"


def _stable_seed(*parts: Any) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return f"{encoded}"


# ---------------------------------------------------------------------------
# ContentBlock 归一化
# ---------------------------------------------------------------------------


def _text_preview(text: str) -> dict[str, Any]:
    length = len(text)
    if length <= TEXT_PREVIEW_CHARS:
        return {"text": text, "text_length": length, "truncated": False}
    return {"text": text[:TEXT_PREVIEW_CHARS], "text_length": length, "truncated": True}


def _data_url_parts(url: str) -> tuple[str | None, str | None]:
    """解析 data URL：返回 (media_type, base64_data)；非 data URL 返回 (None, None)。"""
    if not isinstance(url, str) or not url.startswith("data:"):
        return None, None
    header, _, payload = url.partition(",")
    media_type = header[5:].split(";", 1)[0] or None
    return media_type, payload or None


def _base64_size(data: str) -> int:
    return max(0, len(data) * 3 // 4)


def _image_block(
    *,
    media_type: str | None,
    data: str | None,
    url: str | None,
    filename: str | None,
) -> dict[str, Any]:
    source = "base64" if data else ("url" if url else None)
    previewable = bool(data) and media_type in _PREVIEWABLE_MEDIA_TYPES
    return {
        "type": "image",
        "media_type": media_type,
        "filename": filename,
        "source": source,
        "url": url,
        "size_bytes": _base64_size(data) if data else None,
        "previewable": previewable,
        # full 载荷（仅 block 端点返回，列表视图剔除）。
        "data": data,
    }


def _media_block(
    *,
    block_type: str,
    media_type: str | None,
    data: str | None,
    url: str | None,
    filename: str | None,
) -> dict[str, Any]:
    source = "base64" if data else ("url" if url else None)
    return {
        "type": block_type,
        "media_type": media_type,
        "filename": filename,
        "source": source,
        "url": url,
        "size_bytes": _base64_size(data) if data else None,
        "previewable": False,
        "data": data,
    }


def _tool_call_block(name: Any, arguments: Any, call_id: Any) -> dict[str, Any]:
    args = arguments
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (ValueError, TypeError):
            args = None
    if not isinstance(args, dict):
        args = {}
    preview = json.dumps(args, ensure_ascii=False)
    truncated = len(preview) > TEXT_PREVIEW_CHARS
    return {
        "type": "tool_call",
        "name": name if isinstance(name, str) else None,
        "call_id": call_id if isinstance(call_id, str) else None,
        "arguments": args,
        "arguments_preview": preview[:TEXT_PREVIEW_CHARS],
        "truncated": truncated,
    }


def _tool_result_block(call_id: Any, content: Any) -> dict[str, Any]:
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return {
        "type": "tool_result",
        "call_id": call_id if isinstance(call_id, str) else None,
        **_text_preview(text),
    }


def _unsupported_block(kind: str) -> dict[str, Any]:
    return {"type": "unsupported", "raw_type": kind}


def _chat_part_to_block(part: dict[str, Any]) -> dict[str, Any]:
    ptype = str(part.get("type") or "text")
    if ptype == "text" or ptype == "output_text":
        text = part.get("text")
        return {"type": "text", **_text_preview(text if isinstance(text, str) else "")}
    if ptype == "image_url":
        image = part.get("image_url") if isinstance(part.get("image_url"), dict) else {}
        url = image.get("url")
        media_type, data = _data_url_parts(url if isinstance(url, str) else "")
        if data is not None:
            return _image_block(media_type=media_type, data=data, url=None, filename=None)
        return _image_block(
            media_type=media_type,
            data=None,
            url=url if isinstance(url, str) else None,
            filename=None,
        )
    if ptype == "input_audio":
        audio = part.get("input_audio") if isinstance(part.get("input_audio"), dict) else {}
        data = audio.get("data")
        fmt = audio.get("format")
        media = f"audio/{fmt}" if isinstance(fmt, str) else None
        return _media_block(
            block_type="audio",
            media_type=media,
            data=data if isinstance(data, str) else None,
            url=None,
            filename=None,
        )
    if ptype == "file":
        file_info = part.get("file") if isinstance(part.get("file"), dict) else {}
        url = file_info.get("file_url") or file_info.get("url")
        data = file_info.get("file_data")
        media_type, extracted = _data_url_parts(data if isinstance(data, str) else "")
        if extracted is None:
            extracted = data if isinstance(data, str) else None
        return _media_block(
            block_type="file",
            media_type=media_type,
            data=extracted,
            url=url if isinstance(url, str) else None,
            filename=file_info.get("filename"),
        )
    return _unsupported_block(ptype)


def _anthropic_part_to_block(part: dict[str, Any]) -> dict[str, Any]:
    ptype = str(part.get("type") or "text")
    if ptype == "text":
        text = part.get("text")
        return {"type": "text", **_text_preview(text if isinstance(text, str) else "")}
    if ptype == "image":
        source = part.get("source") if isinstance(part.get("source"), dict) else {}
        source_type = source.get("type")
        if source_type == "base64":
            return _image_block(
                media_type=source.get("media_type"),
                data=source.get("data") if isinstance(source.get("data"), str) else None,
                url=None,
                filename=None,
            )
        return _image_block(media_type=None, data=None, url=source.get("url"), filename=None)
    if ptype == "document":
        source = part.get("source") if isinstance(part.get("source"), dict) else {}
        if source.get("type") == "base64":
            return _media_block(
                block_type="document",
                media_type=source.get("media_type"),
                data=source.get("data") if isinstance(source.get("data"), str) else None,
                url=None,
                filename=source.get("name") or part.get("title"),
            )
        return _media_block(
            block_type="document",
            media_type=None,
            data=None,
            url=source.get("url"),
            filename=source.get("name") or part.get("title"),
        )
    if ptype == "tool_use":
        return _tool_call_block(part.get("name"), part.get("input"), part.get("id"))
    if ptype == "tool_result":
        content = part.get("content")
        if isinstance(content, list):
            texts = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            content = "\n".join(texts)
        return _tool_result_block(part.get("tool_use_id"), content)
    if ptype == "audio":
        source = part.get("source") if isinstance(part.get("source"), dict) else {}
        return _media_block(
            block_type="audio",
            media_type=source.get("type") if isinstance(source.get("type"), str) else None,
            data=source.get("data") if isinstance(source.get("data"), str) else None,
            url=None,
            filename=None,
        )
    return _unsupported_block(ptype)


def _responses_part_to_block(part: dict[str, Any]) -> dict[str, Any]:
    ptype = str(part.get("type") or "input_text")
    if ptype in ("input_text", "output_text", "summary_text"):
        text = part.get("text")
        return {"type": "text", **_text_preview(text if isinstance(text, str) else "")}
    if ptype == "input_image":
        url = part.get("image_url")
        media_type, data = _data_url_parts(url if isinstance(url, str) else "")
        if data is not None:
            return _image_block(media_type=media_type, data=data, url=None, filename=None)
        return _image_block(
            media_type=media_type,
            data=None,
            url=url if isinstance(url, str) else None,
            filename=None,
        )
    if ptype == "input_file":
        data = part.get("file_data")
        media_type, extracted = _data_url_parts(data if isinstance(data, str) else "")
        if extracted is None:
            extracted = data if isinstance(data, str) else None
        return _media_block(
            block_type="file",
            media_type=media_type,
            data=extracted,
            url=part.get("file_url") if isinstance(part.get("file_url"), str) else None,
            filename=part.get("filename"),
        )
    return _unsupported_block(ptype)


_CHAT_PART_TYPES = ("text", "image_url", "input_audio", "file")


def _content_parts_to_blocks(protocol_kind: str, content: Any) -> list[dict[str, Any]]:
    """把消息 content 归一为 ContentBlock 列表。

    protocol_kind ∈ {"chat", "anthropic", "responses"}，决定内容块形态。
    """
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", **_text_preview(content)}]
    if not isinstance(content, list):
        return [_unsupported_block(type(content).__name__)]
    blocks: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            blocks.append(_unsupported_block(type(part).__name__))
            continue
        ptype = str(part.get("type") or "")
        if protocol_kind == "chat":
            blocks.append(
                _chat_part_to_block(part)
                if ptype in _CHAT_PART_TYPES
                else _unsupported_block(ptype)
            )
        elif protocol_kind == "anthropic":
            blocks.append(_anthropic_part_to_block(part))
        else:
            blocks.append(_responses_part_to_block(part))
    return blocks


def _message_blocks(protocol_kind: str, message: dict[str, Any]) -> list[dict[str, Any]]:
    """单条消息（chat/anthropic 形态）的完整 ContentBlock 列表。"""
    blocks = _content_parts_to_blocks(protocol_kind, message.get("content"))
    if protocol_kind == "chat":
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                blocks.append(
                    _tool_call_block(
                        function.get("name"), function.get("arguments"), call.get("id")
                    )
                )
    return blocks


def _responses_item_blocks(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Responses input/context 条目的 ContentBlock 列表。"""
    itype = str(item.get("type") or "message")
    if itype == "message":
        return _content_parts_to_blocks("responses", item.get("content"))
    if itype == "function_call":
        return [_tool_call_block(item.get("name"), item.get("arguments"), item.get("call_id"))]
    if itype == "function_call_output":
        return [_tool_result_block(item.get("call_id"), item.get("output"))]
    return [_unsupported_block(itype)]


# ---------------------------------------------------------------------------
# 协议投影
# ---------------------------------------------------------------------------


def _assign_block_ids(
    blocks: list[dict[str, Any]], section: str, base_seed: str
) -> list[dict[str, Any]]:
    for index, block in enumerate(blocks):
        block["id"] = _opaque_id("blk", _stable_seed(section, base_seed, index, block.get("type")))
    return blocks


def _strip_block_payload(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """列表视图：剔除 base64 正文与完整参数，仅保留摘要字段。"""
    public: list[dict[str, Any]] = []
    for block in blocks:
        entry = {key: value for key, value in block.items() if key not in {"data", "arguments"}}
        if block.get("type") == "tool_call":
            entry["arguments"] = block.get("arguments")
        public.append(entry)
    return public


def _context_item(
    *,
    protocol_kind: str,
    section: str,
    ordinal: int,
    role: str,
    blocks: list[dict[str, Any]],
    context_index: int | None,
) -> tuple[str, dict[str, Any]]:
    seed = _stable_seed(protocol_kind, section, ordinal, role, context_index)
    item_id = _opaque_id("ctx", seed)
    _assign_block_ids(blocks, section, seed)
    text_length = sum(
        int(block.get("text_length") or 0) for block in blocks if block.get("type") == "text"
    )
    item = {
        "id": item_id,
        "role": role,
        "blocks": blocks,
        "text_length": text_length,
        "block_count": len(blocks),
    }
    return item_id, item


def _project_chat_anthropic(protocol_kind: str, normalized: dict[str, Any]) -> dict[str, Any]:
    """Chat / Anthropic：messages 只遍历一次。

    - system/developer（仅 chat）进入 caller_system。
    - 最新一条非 system 消息为 current_input；其余为 attached_context。
    """
    messages = normalized.get("messages")
    if not isinstance(messages, list):
        messages = []
    system_messages = []
    conversation: list[tuple[int, dict[str, Any]]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user")
        if protocol_kind == "chat" and role in _SYSTEM_ROLES:
            system_messages.append((index, message))
            continue
        conversation.append((index, message))

    caller_system_items: list[dict[str, Any]] = []
    system_blocks: list[dict[str, Any]] = []
    if protocol_kind == "anthropic":
        instructions = normalized.get("instructions")
        if isinstance(instructions, str) and instructions.strip():
            blocks = [{"type": "text", **_text_preview(instructions)}]
            _, item = _context_item(
                protocol_kind=protocol_kind,
                section="caller_system",
                ordinal=0,
                role="system",
                blocks=blocks,
                context_index=None,
            )
            caller_system_items.append(item)
            system_blocks.extend(blocks)
        system_block_list = normalized.get("system_blocks")
        if isinstance(system_block_list, list) and system_block_list:
            blocks = _content_parts_to_blocks("anthropic", system_block_list)
            _, item = _context_item(
                protocol_kind=protocol_kind,
                section="caller_system",
                ordinal=1,
                role="system",
                blocks=blocks,
                context_index=None,
            )
            caller_system_items.append(item)
            system_blocks.extend(blocks)
    for ordinal, (index, message) in enumerate(system_messages):
        blocks = _message_blocks(protocol_kind, message)
        _, item = _context_item(
            protocol_kind=protocol_kind,
            section="caller_system",
            ordinal=ordinal,
            role=str(message.get("role") or "system"),
            blocks=blocks,
            context_index=None,
        )
        caller_system_items.append(item)
        system_blocks.extend(blocks)

    attached: list[dict[str, Any]] = []
    attached_map: dict[str, int] = {}
    current_items: list[dict[str, Any]] = []
    if conversation:
        current_index, current_message = conversation[-1]
        blocks = _message_blocks(protocol_kind, current_message)
        _, item = _context_item(
            protocol_kind=protocol_kind,
            section="current_input",
            ordinal=0,
            role=str(current_message.get("role") or "user"),
            blocks=blocks,
            context_index=current_index,
        )
        current_items.append(item)
        for ordinal, (index, message) in enumerate(conversation[:-1]):
            blocks = _message_blocks(protocol_kind, message)
            item_id, item = _context_item(
                protocol_kind=protocol_kind,
                section="attached_context",
                ordinal=ordinal,
                role=str(message.get("role") or "user"),
                blocks=blocks,
                context_index=index,
            )
            attached.append(item)
            attached_map[item_id] = index

    return {
        "current_input": current_items,
        "caller_system": {
            "items": caller_system_items,
            "item_count": len(caller_system_items),
            "character_count": sum(item["text_length"] for item in caller_system_items),
            "collapsed_by_default": True,
        },
        "attached_context": attached,
        "_attached_map": attached_map,
        "_all_blocks": _collect_blocks(current_items, caller_system_items, attached),
    }


def _project_responses(normalized: dict[str, Any]) -> dict[str, Any]:
    """Responses：input 是当前请求主体；展开条目为 attached_context。

    normalized.context = 祖先展开 + 父任务回复项 + 本请求 input 项；
    attached = context 去掉尾部 input 项，不与 input 重复。
    """
    input_value = normalized.get("input")
    context = normalized.get("context")
    if not isinstance(context, list):
        context = []
    if isinstance(input_value, str):
        input_items: list[Any] = [{"type": "message", "role": "user", "content": input_value}]
        input_count = 1
    elif isinstance(input_value, list):
        input_items = list(input_value)
        input_count = len(input_items)
    else:
        input_items = []
        input_count = 0

    instructions = normalized.get("instructions")
    caller_system_items: list[dict[str, Any]] = []
    if isinstance(instructions, str) and instructions.strip():
        blocks = [{"type": "text", **_text_preview(instructions)}]
        _, item = _context_item(
            protocol_kind="responses",
            section="caller_system",
            ordinal=0,
            role="system",
            blocks=blocks,
            context_index=None,
        )
        caller_system_items.append(item)

    current_items: list[dict[str, Any]] = []
    for ordinal, item_raw in enumerate(input_items):
        if not isinstance(item_raw, dict):
            continue
        blocks = _responses_item_blocks(item_raw)
        _, item = _context_item(
            protocol_kind="responses",
            section="current_input",
            ordinal=ordinal,
            role=str(item_raw.get("role") or "user"),
            blocks=blocks,
            context_index=None,
        )
        current_items.append(item)

    attached_count = max(0, len(context) - input_count)
    attached: list[dict[str, Any]] = []
    attached_map: dict[str, int] = {}
    for ordinal, item_raw in enumerate(context[:attached_count]):
        if not isinstance(item_raw, dict):
            continue
        role = str(item_raw.get("role") or "tool")
        blocks = _responses_item_blocks(item_raw)
        item_id, item = _context_item(
            protocol_kind="responses",
            section="attached_context",
            ordinal=ordinal,
            role=role,
            blocks=blocks,
            context_index=ordinal,
        )
        attached.append(item)
        attached_map[item_id] = ordinal

    return {
        "current_input": current_items,
        "caller_system": {
            "items": caller_system_items,
            "item_count": len(caller_system_items),
            "character_count": sum(item["text_length"] for item in caller_system_items),
            "collapsed_by_default": True,
        },
        "attached_context": attached,
        "_attached_map": attached_map,
        "_all_blocks": _collect_blocks(current_items, caller_system_items, attached),
    }


def _collect_blocks(*item_groups: Any) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for group in item_groups:
        if isinstance(group, dict) and "items" in group:
            group = group["items"]
        if not isinstance(group, list):
            continue
        for item in group:
            if isinstance(item, dict) and isinstance(item.get("blocks"), list):
                blocks.extend(item["blocks"])
    return blocks


def project_request_view(protocol_kind: str, normalized: dict[str, Any]) -> dict[str, Any]:
    """投影为 RequestView 分区结构（内部携带 block 全量与 ctx 映射）。"""
    if protocol_kind == "responses":
        return _project_responses(normalized)
    return _project_chat_anthropic(protocol_kind, normalized)


def protocol_kind_of(protocol: Any) -> str:
    value = getattr(protocol, "value", str(protocol))
    return {
        "openai_chat": "chat",
        "anthropic_messages": "anthropic",
        "openai_responses": "responses",
    }.get(value, "chat")


def media_block_present(blocks: list[dict[str, Any]]) -> bool:
    return any(block.get("type") in {"image", "file", "document", "audio"} for block in blocks)


def summarize_attachments(view: dict[str, Any]) -> list[dict[str, Any]]:
    """从分区视图收集附件摘要（不含 base64 正文）。"""
    attachments: list[dict[str, Any]] = []
    for block in view.get("_all_blocks", []):
        if block.get("type") not in {"image", "file", "document", "audio"}:
            continue
        attachments.append(
            {
                "id": block.get("id"),
                "type": block.get("type"),
                "filename": block.get("filename"),
                "media_type": block.get("media_type"),
                "source": block.get("source"),
                "url": block.get("url"),
                "size_bytes": block.get("size_bytes"),
                "previewable": bool(block.get("previewable")),
            }
        )
    return attachments


def public_view(view: dict[str, Any]) -> dict[str, Any]:
    """剥离内部载荷（data/完整 blocks 注册表），得到可序列化的列表视图。"""
    caller_system = view.get("caller_system") or {}
    items = caller_system.get("items") or []
    return {
        "current_input": _strip_items(view.get("current_input")),
        "caller_system": {
            "items": _strip_items(items),
            "item_count": caller_system.get("item_count", len(items)),
            "character_count": caller_system.get("character_count", 0),
            "collapsed_by_default": True,
        },
        "attached_context": _strip_items(view.get("attached_context")),
    }


def _strip_items(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "id": item.get("id"),
                "role": item.get("role"),
                "blocks": _strip_block_payload(item.get("blocks") or []),
                "text_length": item.get("text_length", 0),
                "block_count": item.get("block_count", 0),
            }
        )
    return result


def find_block(view: dict[str, Any], block_id: str) -> dict[str, Any] | None:
    for block in view.get("_all_blocks", []):
        if block.get("id") == block_id:
            return block
    return None


def decode_preview_size(data: str) -> int:
    try:
        return len(base64.b64decode(data, validate=False))
    except (ValueError, TypeError):
        return 0


MAX_ITEMS_GUARD = MAX_EXPANDED_ITEMS
