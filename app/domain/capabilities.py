"""Fake Model 调用侧能力门禁。

任务创建阶段把外部请求与模型声明的能力标签 / 端点列表对齐：
模型未声明的能力直接拒绝（与真实模型行为一致），纯文本、无工具、
非流式的普通对话不依赖任何能力标签，始终放行。

检测对象：
- 规范化请求（normalized）——含 ``previous_response_id`` 展开的历史
  上下文，防止用历史链绕过模态限制；
- 原始请求体顶层字段——思考类控制参数（reasoning / thinking）在
  normalized.options 中受 allowlist 过滤，需从原始负载补检。

拒绝统一返回协议兼容的 400（invalid_request_error）。
"""

from __future__ import annotations

from typing import Any

from .errors import DomainError, DomainErrorCode

# 多模态内容块 type -> 所需能力标签（三协议 type 词表并集）。
_VISION_BLOCK_TYPES = {
    "image_url",  # Chat Completions
    "input_image",  # Responses
    "image",  # Anthropic
    "document",  # Anthropic PDF/文档
    "file",  # Chat 通用文件
    "input_file",  # Responses 文件
}
_AUDIO_BLOCK_TYPES = {
    "input_audio",  # Chat / Responses
    "audio",
}
_VIDEO_BLOCK_TYPES = {
    "video_url",
    "input_video",
}
# 顶层思考控制参数（任一出现即要求 thinking 能力）。
_REASONING_KEYS = {
    "thinking",  # Anthropic
    "reasoning",  # Responses
    "reasoning_effort",  # Chat / 兼容实现
}


def _scan_blocks(value: Any, found: set[str]) -> None:
    """递归扫描内容块，把命中的模态能力写入 found。"""
    if isinstance(value, dict):
        block_type = value.get("type")
        if isinstance(block_type, str):
            if block_type in _VISION_BLOCK_TYPES:
                found.add("vision")
            elif block_type in _AUDIO_BLOCK_TYPES:
                found.add("audio")
            elif block_type in _VIDEO_BLOCK_TYPES:
                found.add("video")
        for child in value.values():
            _scan_blocks(child, found)
    elif isinstance(value, list):
        for item in value:
            _scan_blocks(item, found)


def detect_required_capabilities(
    normalized: dict[str, Any], raw_payload: dict[str, Any], *, stream: bool
) -> set[str]:
    """从规范化请求 + 原始负载推导本次调用所需的能力标签集合。"""
    required: set[str] = set()
    _scan_blocks(normalized.get("context"), required)
    _scan_blocks(normalized.get("system_blocks"), required)
    if normalized.get("tools") or normalized.get("tool_choice"):
        required.add("tools")
    options = normalized.get("options") or {}
    if _REASONING_KEYS & options.keys() or _REASONING_KEYS & raw_payload.keys():
        required.add("thinking")
    if stream:
        required.add("streaming")
    return required


_CAPABILITY_MESSAGES = {
    "vision": "image or file inputs",
    "audio": "audio inputs",
    "video": "video inputs",
    "tools": "tool use",
    "thinking": "reasoning controls",
    "streaming": "streaming",
}


def enforce_model_capabilities(
    declared: list[str] | None,
    *,
    normalized: dict[str, Any],
    raw_payload: dict[str, Any],
    stream: bool,
) -> None:
    """模型未声明请求所需的能力时抛出协议兼容的 400。"""
    declared_set = set(declared or [])
    missing = detect_required_capabilities(normalized, raw_payload, stream=stream) - declared_set
    if not missing:
        return
    label = min(missing)
    raise DomainError(
        DomainErrorCode.INVALID_REQUEST,
        f"The requested model does not support {_CAPABILITY_MESSAGES[label]}.",
        status_code=400,
    )
