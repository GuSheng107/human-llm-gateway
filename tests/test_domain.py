"""领域规则测试：username 归一、密码策略、任务状态机。"""

from __future__ import annotations

import unicodedata

import pytest

from app.domain.enums import TaskState
from app.domain.tasks import can_transition, holds_slot, is_terminal
from app.domain.values import normalize_password, normalize_username, password_problems


class TestUsername:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Alice", "alice"),
            ("  Bob_123  ", "bob_123"),
            ("a.b-c_d", "a.b-c_d"),
        ],
    )
    def test_normalize(self, raw: str, expected: str) -> None:
        assert normalize_username(raw) == expected

    @pytest.mark.parametrize("raw", ["ab", "a", "UPPER!", "山矾", "a" * 65])
    def test_invalid(self, raw: str) -> None:
        assert normalize_username(raw) is None


class TestPassword:
    def test_ok(self) -> None:
        assert password_problems("correct-horse-battery-staple") == []

    def test_too_short(self) -> None:
        assert password_problems("short") != []

    def test_equals_username(self) -> None:
        assert password_problems("alicealicealice", "alicealicealice") != []

    def test_nfc_normalization(self) -> None:
        composed = "é" * 20
        assert normalize_password(composed) == composed

    def test_length_policy_uses_normalized_form(self) -> None:
        decomposed_but_short_after_nfc = unicodedata.normalize("NFD", "é" * 8)
        assert len(decomposed_but_short_after_nfc) >= 15
        assert password_problems(decomposed_but_short_after_nfc) != []


class TestTaskStateMachine:
    def test_terminal_states(self) -> None:
        for state in (
            TaskState.COMPLETED,
            TaskState.FAILED,
            TaskState.TIMED_OUT,
            TaskState.CANCELLED,
        ):
            assert is_terminal(state)
        assert not is_terminal(TaskState.WAITING_HUMAN)

    def test_slot_holding(self) -> None:
        assert holds_slot(TaskState.WAITING_HUMAN)
        assert not holds_slot(TaskState.COMPLETED)

    def test_first_reply_transition(self) -> None:
        assert can_transition(TaskState.WAITING_HUMAN, TaskState.RESPONSE_READY)

    def test_no_transition_from_terminal(self) -> None:
        assert not can_transition(TaskState.COMPLETED, TaskState.CANCELLED)

    def test_illegal_transition(self) -> None:
        assert not can_transition(TaskState.WAITING_HUMAN, TaskState.COMPLETED)
