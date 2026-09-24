"""TypeSafe System One（jev）决策协议（docs/API_CONTRACT.md §17）。

与三个对话协议并列的第四个入站推理协议：调用方提交 ``state``（当前局面）
与一组类型化 ``questions``，网关交由人工裁决，返回类型化 ``answers``
（Noul / Choice / Score）及概率分布。协议无流式、无 Caller Tool。

官方契约（docs.typesafe.ai/api）：
- 请求：``model`` + ``state`` + ``questions``（map），可选 ``instructions``。
- 三种问题：
  - ``noul``：criteria 可选（仅 ``true`` / ``false`` 描述）；答案 noul ∈ [0,1]。
  - ``choice``：criteria 必填（2-255 个选项）；答案含 choice + probabilities。
  - ``score``：criteria 必填（有序数组 2-10 级，低->高）；答案含 score + legend。
- 响应：``{model, answers, usage:{input_tokens, output_tokens}}``。

人工答案经工作台以 ``final_text`` 提交 JSON ``{"answers": {...}}``；渲染时
按请求 questions 校验键与类型，非法答案返回 500 upstream_error。
"""

from __future__ import annotations

import json
from typing import Any

from ..domain.errors import DomainError, DomainErrorCode
from ..domain.values import ReplyDraft
from .normalized import decode_object

_QUESTION_TYPES = {"noul", "choice", "score"}
_MIN_CHOICE_OPTIONS = 2
_MAX_CHOICE_OPTIONS = 255
_MIN_SCORE_LEVELS = 2
_MAX_SCORE_LEVELS = 10
_NOUL_CRITERIA_KEYS = {"true", "false"}


def _fail(message: str) -> DomainError:
    """jev 官方以 422 表示请求校验失败。"""
    return DomainError(DomainErrorCode.INVALID_REQUEST, message, status_code=422)


def _bad_answer(message: str) -> DomainError:
    """人工提交的答案不符合协议：500，不泄露人工流程细节。"""
    return DomainError(DomainErrorCode.UPSTREAM_ERROR, message, status_code=500)


def _require_state(payload: dict[str, Any]) -> Any:
    if "state" not in payload or payload["state"] is None:
        raise _fail("state 为必填字段")
    state = payload["state"]
    if not isinstance(state, (str, dict, list)):
        raise _fail("state 必须是字符串、对象或数组")
    if isinstance(state, str) and not state.strip():
        raise _fail("state 不能为空字符串")
    return state


def _validate_noul_criteria(question_id: str, criteria: Any) -> None:
    if criteria is None:
        return
    if not isinstance(criteria, dict):
        raise _fail(f"questions.{question_id}.criteria（noul）必须是对象")
    for key in criteria:
        if key not in _NOUL_CRITERIA_KEYS:
            raise _fail(f"questions.{question_id}.criteria 仅允许 true / false 键")


def _validate_noul(question_id: str, question: dict[str, Any], criteria: Any) -> None:
    """noul 的 criteria 与 instructions 至少其一（官方真机 400 行为）。"""
    _validate_noul_criteria(question_id, criteria)
    instructions = question.get("instructions")
    if criteria is None and instructions is None:
        raise _fail(f"questions.{question_id}（noul）必须提供 criteria 或 instructions")


def _validate_choice_criteria(question_id: str, question: dict[str, Any], criteria: Any) -> None:
    if not isinstance(criteria, dict) or not criteria:
        raise _fail(f"questions.{question_id}.criteria（choice）必须是非空对象")
    count = len(criteria)
    if count < _MIN_CHOICE_OPTIONS or count > _MAX_CHOICE_OPTIONS:
        raise _fail(
            f"questions.{question_id}.criteria 选项数必须在 "
            f"{_MIN_CHOICE_OPTIONS}-{_MAX_CHOICE_OPTIONS} 之间"
        )
    for option in criteria:
        if not isinstance(option, str) or not option.strip():
            raise _fail(f"questions.{question_id}.criteria 的选项键必须是非空字符串")


def _validate_score_criteria(question_id: str, question: dict[str, Any], criteria: Any) -> None:
    if not isinstance(criteria, list) or not criteria:
        raise _fail(f"questions.{question_id}.criteria（score）必须是非空数组")
    count = len(criteria)
    if count < _MIN_SCORE_LEVELS or count > _MAX_SCORE_LEVELS:
        raise _fail(
            f"questions.{question_id}.criteria 级别数必须在 "
            f"{_MIN_SCORE_LEVELS}-{_MAX_SCORE_LEVELS} 之间"
        )


_CRITERIA_VALIDATORS = {
    "noul": _validate_noul,
    "choice": _validate_choice_criteria,
    "score": _validate_score_criteria,
}


def _validate_question(question_id: str, question: Any) -> None:
    if not isinstance(question, dict):
        raise _fail(f"questions.{question_id} 必须是对象")
    qtype = question.get("type")
    if qtype not in _QUESTION_TYPES:
        raise _fail(f"questions.{question_id}.type 必须是 noul / choice / score 之一")
    instructions = question.get("instructions")
    if instructions is not None and not isinstance(instructions, (str, dict, list)):
        raise _fail(f"questions.{question_id}.instructions 必须是字符串、对象或数组")
    # choice / score 的 criteria 必填；noul 可选但需与 instructions 至少其一。
    criteria = question.get("criteria")
    if qtype in {"choice", "score"} and criteria is None:
        raise _fail(f"questions.{question_id}.criteria（{qtype}）为必填字段")
    _CRITERIA_VALIDATORS[qtype](question_id, question, criteria)


def _validate_questions(payload: dict[str, Any]) -> dict[str, Any]:
    questions = payload.get("questions")
    if not isinstance(questions, dict) or not questions:
        raise _fail("questions 必须是非空对象（map）")
    for question_id, question in questions.items():
        if not isinstance(question_id, str) or not question_id.strip():
            raise _fail("questions 的键必须是非空字符串")
        _validate_question(question_id, question)
    return questions


class SystemOneRequest:
    """jev System One 请求（构造期完成全部校验）。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        model = payload.get("model")
        if not isinstance(model, str) or not model.strip():
            raise _fail("model 必须是非空字符串")
        self.model = model
        self.state = _require_state(payload)
        self.questions = _validate_questions(payload)
        instructions = payload.get("instructions")
        if instructions is not None and not isinstance(instructions, (str, dict, list)):
            raise _fail("instructions 必须是字符串、对象或数组")
        self.instructions = instructions
        # 决策协议无流式语义。
        self.stream = False
        self.raw = payload

    def normalized_request(self) -> dict[str, Any]:
        """规范化为三协议共享结构：state 作为单条上下文，questions 独立承载。"""
        return {
            "context": [{"role": "state", "state": self.state}],
            "instructions": self.instructions if isinstance(self.instructions, str) else None,
            "tools": None,
            "tool_choice": None,
            "options": {},
            "state": self.state,
            "questions": self.questions,
            "stream": False,
        }


def parse_request(raw: bytes) -> SystemOneRequest:
    return SystemOneRequest(decode_object(raw, label="jev 请求体"))


def _score_legend(criteria: Any) -> dict[str, str]:
    """score criteria（有序数组，低->高）-> legend {"0".."n": 描述}。"""
    legend: dict[str, str] = {}
    if not isinstance(criteria, list):
        return legend
    for index, level in enumerate(criteria):
        if isinstance(level, str):
            legend[str(index)] = level
        elif isinstance(level, dict):
            legend[str(index)] = str(
                level.get("description")
                or level.get("label")
                or json.dumps(level, ensure_ascii=False)
            )
        else:
            legend[str(index)] = str(level)
    return legend


def _normalize_answer(question_id: str, question: dict[str, Any], answer: Any) -> dict[str, Any]:
    """校验并补全单个答案；非法答案抛 500（人工提交内容不符合协议）。"""
    qtype = question.get("type")
    if not isinstance(answer, dict):
        raise _bad_answer(f"answers.{question_id} 必须是对象")
    if answer.get("type") != qtype:
        raise _bad_answer(f"answers.{question_id}.type 与问题类型不一致")
    if qtype == "noul":
        return _normalize_noul(question_id, answer)
    if qtype == "choice":
        return _normalize_choice(question_id, question, answer)
    return _normalize_score(question_id, question, answer)


def _normalize_unit(value: Any) -> float:
    """校验 [0,1] 数值（概率 / 置信度共用）；非法抛 500。"""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise _bad_answer("概率与置信度必须是数值")
    unit = float(value)
    if not 0.0 <= unit <= 1.0:
        raise _bad_answer("概率与置信度必须在 0-1 之间")
    return unit


def _normalize_probabilities(
    question_id: str,
    answer: dict[str, Any],
    allowed: set[str] | None,
    default: dict[str, float],
) -> dict[str, float]:
    """校验 probabilities：可选；提供时键必须命中候选、值必须在 [0,1]。"""
    raw = answer.get("probabilities")
    if raw is None:
        return default
    if not isinstance(raw, dict) or not raw:
        raise _bad_answer(f"answers.{question_id}.probabilities 必须是非空对象")
    probabilities: dict[str, float] = {}
    for key, value in raw.items():
        if allowed is not None and key not in allowed:
            raise _bad_answer(f"answers.{question_id}.probabilities 含未知选项")
        probabilities[str(key)] = _normalize_unit(value)
    return probabilities


def _normalize_noul(question_id: str, answer: dict[str, Any]) -> dict[str, Any]:
    return {"type": "noul", "noul": _normalize_unit(answer.get("noul"))}


def _normalize_choice(
    question_id: str, question: dict[str, Any], answer: dict[str, Any]
) -> dict[str, Any]:
    choice = answer.get("choice")
    criteria = question.get("criteria")
    options = list(criteria.keys()) if isinstance(criteria, dict) else []
    if choice not in options:
        raise _bad_answer(f"answers.{question_id}.choice 不在候选选项中")
    probabilities = _normalize_probabilities(question_id, answer, set(options), {choice: 1.0})
    confidence = answer.get("confidence")
    return {
        "type": "choice",
        "choice": choice,
        "probabilities": probabilities,
        "confidence": _normalize_unit(confidence) if confidence is not None else 1.0,
    }


def _normalize_score(
    question_id: str, question: dict[str, Any], answer: dict[str, Any]
) -> dict[str, Any]:
    score = answer.get("score")
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        raise _bad_answer(f"answers.{question_id}.score 必须是数值")
    criteria = question.get("criteria")
    max_level = (len(criteria) - 1) if isinstance(criteria, list) else 0
    if not 0.0 <= float(score) <= float(max_level):
        raise _bad_answer(f"answers.{question_id}.score 超出级别范围")
    legend = answer.get("legend")
    if legend is None:
        legend = _score_legend(criteria)
    elif not isinstance(legend, dict):
        raise _bad_answer(f"answers.{question_id}.legend 必须是对象")
    probabilities = _normalize_probabilities(
        question_id, answer, None, {str(round(float(score))): 1.0}
    )
    confidence = answer.get("confidence")
    return {
        "type": "score",
        "score": float(score),
        "legend": legend,
        "probabilities": probabilities,
        "confidence": _normalize_unit(confidence) if confidence is not None else 1.0,
    }


def _parse_answers(draft: ReplyDraft, questions: dict[str, Any]) -> dict[str, Any]:
    text = (draft.final_text or "").strip()
    if not text:
        raise _bad_answer("人工回复缺少 jev 决策答案")
    try:
        payload = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise _bad_answer("人工回复不是合法的 jev 答案 JSON") from exc
    answers = payload.get("answers") if isinstance(payload, dict) else None
    if not isinstance(answers, dict):
        raise _bad_answer("人工回复缺少 answers 对象")
    if set(answers.keys()) != set(questions.keys()):
        raise _bad_answer("answers 的键必须与 questions 完全一致")
    return {
        question_id: _normalize_answer(question_id, questions[question_id], answers[question_id])
        for question_id in questions
    }


def render_response(
    model: str,
    draft: ReplyDraft,
    questions: dict[str, Any],
    *,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    """ReplyDraft -> jev 响应：{model, answers, usage}。"""
    answers = _parse_answers(draft, questions)
    used = usage or {}
    return {
        "model": model,
        "answers": answers,
        "usage": {
            "input_tokens": used.get("input_tokens", 0),
            "output_tokens": used.get("output_tokens", 0),
        },
    }
