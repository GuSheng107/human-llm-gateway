"""任务状态机（见 docs/ARCHITECTURE.md §6）。"""

from __future__ import annotations

from .enums import TaskState

# 终态：进入后释放用户活动名额，且不可再转换。
TERMINAL_STATES: frozenset[TaskState] = frozenset(
    {
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.TIMED_OUT,
        TaskState.CANCELLED,
    }
)

# 占用活动名额的状态。
SLOT_HOLDING_STATES: frozenset[TaskState] = frozenset(
    {
        TaskState.RECEIVED,
        TaskState.WAITING_HUMAN,
        TaskState.FORWARDING_LLM,
        TaskState.RESPONSE_READY,
        TaskState.RESPONDING,
    }
)

# 合法状态转换（正向）。
_ALLOWED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.RECEIVED: frozenset(
        {
            TaskState.WAITING_HUMAN,
            TaskState.FORWARDING_LLM,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.WAITING_HUMAN: frozenset(
        {
            TaskState.RESPONSE_READY,
            TaskState.FORWARDING_LLM,
            TaskState.TIMED_OUT,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.FORWARDING_LLM: frozenset(
        {
            TaskState.RESPONSE_READY,
            TaskState.RESPONDING,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.RESPONSE_READY: frozenset(
        {
            TaskState.RESPONDING,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.RESPONDING: frozenset(
        {
            TaskState.COMPLETED,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
}


def is_terminal(state: TaskState) -> bool:
    return state in TERMINAL_STATES


def holds_slot(state: TaskState) -> bool:
    return state in SLOT_HOLDING_STATES


def can_transition(from_state: TaskState, to_state: TaskState) -> bool:
    """是否允许 from -> to 的单步转换；终态不可再转换。"""
    if is_terminal(from_state):
        return False
    return to_state in _ALLOWED_TRANSITIONS.get(from_state, frozenset())
