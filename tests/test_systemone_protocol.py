"""jev（TypeSafe System One）决策协议测试（docs/API_CONTRACT.md §17）。

覆盖：请求解析校验（noul / choice / score + 422）、规范化结构、答案渲染
（合法 + 非法 500）、Caller Tool 空目录、RequestView 投影、LLM 策略守卫、
端点鉴权 / 404 / 422 / happy。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from sqlalchemy import select

import app.core.db as database
from app.domain.caller_tools import build_caller_tool_catalog
from app.domain.enums import InferenceProtocol, ReplyStrategy
from app.domain.errors import DomainError
from app.domain.request_view import project_request_view, protocol_kind_of
from app.domain.values import ReplyDraft
from app.protocols import systemone as so
from app.repositories.models import RequestTask
from app.services.inference_service import InferenceService

# ----------------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------------


def _payload(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "model": "deepseek-v4-pro",
        "state": {"text": "hello"},
        "questions": {
            "is_urgent": {"type": "noul", "criteria": {"true": "urgent", "false": "not"}}
        },
    }
    base.update(extra)
    return base


def _draft(answers: dict[str, Any]) -> ReplyDraft:
    return ReplyDraft(final_text=json.dumps({"answers": answers}))


def _bearer(plaintext: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {plaintext}"}


# ----------------------------------------------------------------------
# 请求解析校验
# ----------------------------------------------------------------------


def test_parse_noul_ok() -> None:
    req = so.SystemOneRequest(
        {
            "model": "m",
            "state": "s",
            "questions": {"q": {"type": "noul", "criteria": {"true": "yes", "false": "no"}}},
        }
    )
    assert req.model == "m"
    assert req.stream is False


def test_parse_noul_instructions_only_ok() -> None:
    """官方允许 noul 只带 instructions（真机 200 验证）。"""
    req = so.SystemOneRequest(
        {
            "model": "m",
            "state": "s",
            "questions": {"q": {"type": "noul", "instructions": "1.0 if urgent"}},
        }
    )
    assert req.questions["q"]["type"] == "noul"


def test_parse_choice_ok() -> None:
    req = so.SystemOneRequest(
        {
            "model": "m",
            "state": {"a": 1},
            "questions": {"q": {"type": "choice", "criteria": {"a": "A", "b": "B"}}},
        }
    )
    assert req.questions["q"]["type"] == "choice"


def test_parse_score_ok() -> None:
    req = so.SystemOneRequest(
        {
            "model": "m",
            "state": [1, 2],
            "questions": {"q": {"type": "score", "criteria": ["low", "med", "high"]}},
        }
    )
    assert req.questions["q"]["type"] == "score"


def test_parse_missing_model_422() -> None:
    with pytest.raises(DomainError) as ei:
        so.SystemOneRequest({"state": "s", "questions": {"q": {"type": "noul"}}})
    assert ei.value.status_code == 422


def test_parse_missing_state_422() -> None:
    with pytest.raises(DomainError) as ei:
        so.SystemOneRequest({"model": "m", "questions": {"q": {"type": "noul"}}})
    assert ei.value.status_code == 422


def test_parse_empty_questions_422() -> None:
    with pytest.raises(DomainError) as ei:
        so.SystemOneRequest({"model": "m", "state": "s", "questions": {}})
    assert ei.value.status_code == 422


def test_parse_invalid_question_type_422() -> None:
    with pytest.raises(DomainError):
        so.SystemOneRequest({"model": "m", "state": "s", "questions": {"q": {"type": "bogus"}}})


def test_parse_choice_criteria_too_few_422() -> None:
    with pytest.raises(DomainError):
        so.SystemOneRequest(
            {
                "model": "m",
                "state": "s",
                "questions": {"q": {"type": "choice", "criteria": {"a": "A"}}},
            }
        )


def test_parse_choice_criteria_too_many_422() -> None:
    criteria = {f"o{i}": str(i) for i in range(256)}
    with pytest.raises(DomainError):
        so.SystemOneRequest(
            {
                "model": "m",
                "state": "s",
                "questions": {"q": {"type": "choice", "criteria": criteria}},
            }
        )


def test_parse_score_criteria_too_many_422() -> None:
    with pytest.raises(DomainError):
        so.SystemOneRequest(
            {
                "model": "m",
                "state": "s",
                "questions": {"q": {"type": "score", "criteria": [str(i) for i in range(11)]}},
            }
        )


def test_parse_noul_criteria_invalid_key_422() -> None:
    with pytest.raises(DomainError):
        so.SystemOneRequest(
            {
                "model": "m",
                "state": "s",
                "questions": {"q": {"type": "noul", "criteria": {"yes": "y"}}},
            }
        )


def test_parse_choice_missing_criteria_422() -> None:
    with pytest.raises(DomainError):
        so.SystemOneRequest({"model": "m", "state": "s", "questions": {"q": {"type": "choice"}}})


def test_parse_noul_bare_422() -> None:
    """noul 既无 criteria 也无 instructions：官方真机 400，网关 422。"""
    with pytest.raises(DomainError) as ei:
        so.SystemOneRequest({"model": "m", "state": "s", "questions": {"q": {"type": "noul"}}})
    assert ei.value.status_code == 422


def test_normalized_request_shape() -> None:
    questions = {"q": {"type": "noul", "instructions": "0-1 urgent probability"}}
    req = so.SystemOneRequest(
        {
            "model": "m",
            "state": {"a": 1},
            "questions": questions,
            "instructions": "do it",
        }
    )
    norm = req.normalized_request()
    assert norm["context"] == [{"role": "state", "state": {"a": 1}}]
    assert norm["instructions"] == "do it"
    assert norm["tools"] is None
    assert norm["options"] == {}
    assert norm["questions"] == questions
    assert norm["stream"] is False


# ----------------------------------------------------------------------
# 答案渲染
# ----------------------------------------------------------------------


def test_render_noul_ok() -> None:
    resp = so.render_response(
        "m", _draft({"q": {"type": "noul", "noul": 0.5}}), {"q": {"type": "noul"}}
    )
    assert resp["model"] == "m"
    assert resp["answers"]["q"] == {"type": "noul", "noul": 0.5}
    assert set(resp["usage"]) == {"input_tokens", "output_tokens"}


def test_render_choice_ok() -> None:
    questions = {"pick": {"type": "choice", "criteria": {"a": "A", "b": "B"}}}
    answer = {
        "pick": {
            "type": "choice",
            "choice": "a",
            "probabilities": {"a": 0.6, "b": 0.4},
            "confidence": 0.7,
        }
    }
    resp = so.render_response("m", _draft(answer), questions)
    assert resp["answers"]["pick"]["choice"] == "a"
    assert resp["answers"]["pick"]["probabilities"] == {"a": 0.6, "b": 0.4}


def test_render_score_ok_with_legend() -> None:
    questions = {"sev": {"type": "score", "criteria": ["low", "med", "high"]}}
    answer = {"sev": {"type": "score", "score": 1, "probabilities": {"1": 1.0}, "confidence": 0.9}}
    resp = so.render_response("m", _draft(answer), questions)
    scored = resp["answers"]["sev"]
    assert scored["score"] == 1.0
    assert scored["legend"] == {"0": "low", "1": "med", "2": "high"}


def test_render_invalid_json_500() -> None:
    with pytest.raises(DomainError) as ei:
        so.render_response("m", ReplyDraft(final_text="not json"), {"q": {"type": "noul"}})
    assert ei.value.status_code == 500


def test_render_missing_answers_500() -> None:
    with pytest.raises(DomainError):
        so.render_response("m", ReplyDraft(final_text="{}"), {"q": {"type": "noul"}})


def test_render_key_mismatch_500() -> None:
    with pytest.raises(DomainError):
        so.render_response(
            "m", _draft({"other": {"type": "noul", "noul": 0.5}}), {"q": {"type": "noul"}}
        )


def test_render_choice_not_in_options_500() -> None:
    questions = {"pick": {"type": "choice", "criteria": {"a": "A", "b": "B"}}}
    answer = {
        "pick": {"type": "choice", "choice": "z", "probabilities": {"z": 1.0}, "confidence": 1}
    }
    with pytest.raises(DomainError):
        so.render_response("m", _draft(answer), questions)


def test_render_noul_out_of_range_500() -> None:
    with pytest.raises(DomainError):
        so.render_response(
            "m", _draft({"q": {"type": "noul", "noul": 1.5}}), {"q": {"type": "noul"}}
        )


def test_render_legend_not_object_500() -> None:
    """人工提交的 legend 必须是对象，数组透传会破坏协议。"""
    questions = {"sev": {"type": "score", "criteria": ["low", "high"]}}
    answer = {"sev": {"type": "score", "score": 0, "legend": ["low", "high"]}}
    with pytest.raises(DomainError) as ei:
        so.render_response("m", _draft(answer), questions)
    assert ei.value.status_code == 500


def test_render_probabilities_unknown_key_500() -> None:
    questions = {"pick": {"type": "choice", "criteria": {"a": "A", "b": "B"}}}
    answer = {"pick": {"type": "choice", "choice": "a", "probabilities": {"zz": 1.0}}}
    with pytest.raises(DomainError):
        so.render_response("m", _draft(answer), questions)


def test_render_probabilities_out_of_range_500() -> None:
    questions = {"pick": {"type": "choice", "criteria": {"a": "A", "b": "B"}}}
    answer = {"pick": {"type": "choice", "choice": "a", "probabilities": {"a": 1.5}}}
    with pytest.raises(DomainError):
        so.render_response("m", _draft(answer), questions)


def test_render_confidence_out_of_range_500() -> None:
    questions = {"pick": {"type": "choice", "criteria": {"a": "A", "b": "B"}}}
    answer = {"pick": {"type": "choice", "choice": "a", "confidence": 3.0}}
    with pytest.raises(DomainError):
        so.render_response("m", _draft(answer), questions)


# ----------------------------------------------------------------------
# Caller Tool / RequestView / 策略守卫
# ----------------------------------------------------------------------


def test_caller_tools_systemone_empty() -> None:
    catalog = build_caller_tool_catalog(
        InferenceProtocol.TYPE_SAFE_SYSTEMONE,
        {"tools": [{"name": "x"}], "tool_choice": "required"},
    )
    assert catalog.is_empty
    assert catalog.policy.choice.value == "auto"


def test_protocol_kind_of_systemone() -> None:
    assert protocol_kind_of(InferenceProtocol.TYPE_SAFE_SYSTEMONE) == "systemone"


def test_project_systemone_view() -> None:
    norm = {"state": {"a": 1}, "questions": {"q": {"type": "noul"}}, "instructions": "ins"}
    view = project_request_view("systemone", norm)
    assert len(view["current_input"]) == 1
    assert view["current_input"][0]["role"] == "state"
    roles = [item["role"] for item in view["caller_system"]["items"]]
    assert "instructions" in roles
    assert "questions" in roles
    assert view["attached_context"] == []


def test_guard_rejects_llm() -> None:
    with pytest.raises(DomainError) as ei:
        InferenceService._assert_protocol_strategy(
            InferenceProtocol.TYPE_SAFE_SYSTEMONE, ReplyStrategy.LLM
        )
    assert ei.value.status_code == 400


def test_guard_rejects_human_fallback_llm() -> None:
    with pytest.raises(DomainError):
        InferenceService._assert_protocol_strategy(
            InferenceProtocol.TYPE_SAFE_SYSTEMONE, ReplyStrategy.HUMAN_FALLBACK_LLM
        )


def test_guard_allows_human() -> None:
    InferenceService._assert_protocol_strategy(
        InferenceProtocol.TYPE_SAFE_SYSTEMONE, ReplyStrategy.HUMAN
    )


# ----------------------------------------------------------------------
# 端点
# ----------------------------------------------------------------------


def test_endpoint_no_key_401(client) -> None:
    resp = client.post("/v1/systemone", json=_payload())
    assert resp.status_code == 401
    assert "error" in resp.json()


def test_endpoint_model_not_found_404(client, created_key) -> None:
    resp = client.post(
        "/v1/systemone",
        headers=_bearer(created_key.plaintext),
        json=_payload(model="no-such-model"),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "model_not_found"


def test_endpoint_validation_422(client, created_key) -> None:
    resp = client.post(
        "/v1/systemone",
        headers=_bearer(created_key.plaintext),
        json={"model": "deepseek-v4-pro", "questions": {"q": {"type": "noul"}}},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unprocessable_entity"


@pytest.fixture
async def async_client(client) -> Any:
    transport = httpx.ASGITransport(app=client.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _latest_task_id(api_key_id: int) -> int:
    with database.SessionLocal() as session:
        row = (
            session.execute(
                select(RequestTask)
                .where(RequestTask.api_key_id == api_key_id)
                .order_by(RequestTask.id.desc())
            )
            .scalars()
            .first()
        )
        assert row is not None, "未找到任务"
        return row.id


async def _wait_task_id(api_key_id: int, timeout: float = 10.0) -> int:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        try:
            return _latest_task_id(api_key_id)
        except AssertionError:
            if loop.time() >= deadline:
                raise
            await asyncio.sleep(0.05)


def _submit(task_id: int, owner_id: int, text: str) -> None:
    payload = ReplyDraft(final_text=text).model_dump_json(exclude_none=True)
    with database.SessionLocal() as session:
        task = session.get(RequestTask, task_id)
        InferenceService().tasks.first_reply_wins(
            session,
            task_id=task_id,
            owner_user_id=owner_id,
            expected_version=task.version,
            response_payload_json=payload,
        )
        session.commit()


@pytest.mark.asyncio
async def test_endpoint_happy_nonstream(async_client, created_key) -> None:
    answers = {"is_urgent": {"type": "noul", "noul": 0.95}}

    async def reply_later() -> None:
        task_id = await _wait_task_id(created_key.id)
        _submit(task_id, created_key.owner_user_id, json.dumps({"answers": answers}))

    runner = asyncio.create_task(reply_later())
    resp = await async_client.post(
        "/v1/systemone",
        headers=_bearer(created_key.plaintext),
        json=_payload(),
    )
    await runner
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model"] == "deepseek-v4-pro"
    assert body["answers"]["is_urgent"] == {"type": "noul", "noul": 0.95}
    assert set(body["usage"]) == {"input_tokens", "output_tokens"}
