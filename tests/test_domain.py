"""领域规则测试：username 归一、密码策略、任务状态机。"""

from __future__ import annotations

import unicodedata

import pytest

from app.domain.enums import TaskState
from app.domain.tasks import can_transition, holds_slot, is_terminal
from app.domain.values import (
    normalize_avatar_base64,
    normalize_email,
    normalize_password,
    normalize_username,
    password_problems,
)


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
        assert password_problems("Correct-Horse1!") == []

    @pytest.mark.parametrize(
        "password",
        [
            "CorrectHorse",  # 缺数字、缺符号
            "123456789012",  # 缺字母、缺符号
            "!@#$%^&*()_+",  # 缺字母、缺数字
            "CorrectHorse1",  # 缺符号
            "CorrectHorse!",  # 缺数字
            "1234567890!",  # 缺字母
        ],
    )
    def test_missing_required_character_class(self, password: str) -> None:
        assert password_problems(password) != []

    def test_too_short(self) -> None:
        assert password_problems("short") != []

    def test_equals_username(self) -> None:
        problems = password_problems("alicealicealice", "alicealicealice")
        assert "密码不能与用户名相同" in problems

    def test_nfc_normalization(self) -> None:
        composed = "é" * 20
        assert normalize_password(composed) == composed

    def test_length_policy_uses_normalized_form(self) -> None:
        decomposed_but_short_after_nfc = unicodedata.normalize("NFD", "é" * 6)
        assert len(decomposed_but_short_after_nfc) >= 10
        assert password_problems(decomposed_but_short_after_nfc) != []


class TestEmail:
    def test_normalize(self) -> None:
        assert normalize_email("  User@Example.COM ") == "user@example.com"
        assert normalize_email("a.b-c_d+tag@sub.example.io") == "a.b-c_d+tag@sub.example.io"

    def test_invalid(self) -> None:
        assert normalize_email("bad-email") is None
        assert normalize_email("a@b.c") is None  # TLD 至少两位
        assert normalize_email("a b@example.com") is None
        assert normalize_email(None) is None
        assert normalize_email("") is None


class TestAvatar:
    _PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="

    def test_accept_png(self) -> None:
        assert normalize_avatar_base64(self._PNG) == self._PNG

    def test_accept_data_url(self) -> None:
        assert normalize_avatar_base64("data:image/png;base64," + self._PNG) == self._PNG

    def test_empty_clears(self) -> None:
        assert normalize_avatar_base64("") == ""

    def test_none_means_no_change(self) -> None:
        assert normalize_avatar_base64(None) is None

    def test_reject_invalid(self) -> None:
        assert normalize_avatar_base64("not-base64!!") is None
        assert normalize_avatar_base64("aGVsbG8=") is None  # 非图片


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
