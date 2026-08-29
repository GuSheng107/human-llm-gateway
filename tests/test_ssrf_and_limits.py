"""SSRF 防护与上游资源上限测试。

覆盖：
- 云元数据/链路本地地址无条件拒绝（无开关，字面 IP 与域名解析路径）
- 私有/回网段默认拒绝、LLM_ALLOW_PRIVATE_UPSTREAM=true 放行
- 配置创建/更新两条路径的拒绝；DNS 解析失败 fail-closed
- 请求前 rebinding 防护（配置通过后 DNS 指向内网 -> 请求期拒绝）
- 连通性测试前校验（blocked reason_code）
- 流式预算：累计字节/单行超限
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.core.ssrf import SsrfViolation, validate_base_url
from app.domain.errors import DomainError, DomainErrorCode

# ----------------------------------------------------------------------
# 分档：云元数据无条件拒绝
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "base_url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.0.5/",
        "http://100.100.100.200/latest/meta-data/",
        "http://168.63.129.16/machine/",
        "http://metadata.google.internal/computeMetadata/v1/",
    ],
)
def test_metadata_addresses_rejected_unconditionally(base_url: str) -> None:
    with pytest.raises(SsrfViolation):
        validate_base_url(base_url)


def test_metadata_rejected_even_with_private_switch_enabled(monkeypatch) -> None:
    """开关放行私有段时，云元数据仍无条件拒绝。"""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "llm_allow_private_upstream", True, raising=False)
    with pytest.raises(SsrfViolation):
        validate_base_url("http://169.254.169.254/latest/meta-data/")


# ----------------------------------------------------------------------
# 分档：私有段默认拒 / 开关放行
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:11434/v1",  # Ollama
        "http://10.0.0.5:8080/",
        "http://172.16.1.1/",
        "http://192.168.1.100/v1",
        "http://[::1]:8080/",
    ],
)
def test_private_addresses_rejected_by_default(base_url: str) -> None:
    with pytest.raises(SsrfViolation) as exc:
        validate_base_url(base_url)
    assert "LLM_ALLOW_PRIVATE_UPSTREAM" in str(exc.value)


def test_private_addresses_allowed_with_switch(monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "llm_allow_private_upstream", True, raising=False)
    validate_base_url("http://127.0.0.1:11434/v1")  # 不抛
    validate_base_url("http://10.0.0.5:8080/")


def test_public_address_allowed_by_default() -> None:
    # 字面公网 IP 无需 DNS
    validate_base_url("https://93.184.216.34/v1")


# ----------------------------------------------------------------------
# 域名解析路径（rebinding / fail-closed）
# ----------------------------------------------------------------------


def test_domain_resolving_to_private_rejected(monkeypatch) -> None:
    import socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(2, 1, 6, "", ("10.1.2.3", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(SsrfViolation):
        validate_base_url("https://evil-rebind.example.com/v1")


def test_domain_resolving_to_metadata_rejected(monkeypatch) -> None:
    import socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(2, 1, 6, "", ("169.254.169.254", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(SsrfViolation):
        validate_base_url("https://evil-rebind.example.com/v1")


def test_dns_failure_fail_closed(monkeypatch) -> None:
    import socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        raise OSError("NXDOMAIN")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(SsrfViolation):
        validate_base_url("https://unresolvable.example.com/v1")


def test_domain_to_public_allowed(monkeypatch) -> None:
    import socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    validate_base_url("https://api.example.com/v1")


# ----------------------------------------------------------------------
# 配置创建/更新路径
# ----------------------------------------------------------------------


def _config_body(base_url: str) -> dict[str, Any]:
    return {
        "name": "ssrf-probe",
        "protocol": "openai_compatible",
        "base_url": base_url,
        "api_key": "sk",
        "model": "gpt-4o-mini",
        "timeout_seconds": 60,
    }


def test_create_config_with_metadata_url_rejected(client, created_user) -> None:
    resp = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json=_config_body("http://169.254.169.254/latest/"),
    )
    assert resp.status_code == 400
    assert "云元数据" in resp.json()["error"]["message"]


def test_create_config_with_private_url_rejected_by_default(client, created_user) -> None:
    resp = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json=_config_body("http://127.0.0.1:11434/v1"),
    )
    assert resp.status_code == 400
    assert "LLM_ALLOW_PRIVATE_UPSTREAM" in resp.json()["error"]["message"]


def test_update_config_to_private_url_rejected(client, created_user) -> None:
    created = client.post(
        "/api/llm-configs",
        headers=created_user.headers,
        json=_config_body("https://api.example.com/v1"),
    ).json()
    resp = client.patch(
        f"/api/llm-configs/{created['id']}",
        headers=created_user.headers,
        json={"base_url": "http://10.0.0.9/"},
    )
    assert resp.status_code == 400


# ----------------------------------------------------------------------
# 请求前 rebinding 防护（配置通过、请求时 DNS 指向内网）
# ----------------------------------------------------------------------


def test_precheck_blocks_rebound_domain() -> None:

    from app.services.llm_upstream import _precheck_ssrf

    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.9.9.9", 0))]):
        import asyncio

        with pytest.raises(DomainError) as exc:
            asyncio.run(_precheck_ssrf("https://api.example.com/v1"))
    assert exc.value.code is DomainErrorCode.UPSTREAM_ERROR


def test_post_chat_completions_prechecks_before_request() -> None:
    """上游调用前执行 SSRF 校验：内网字面 IP 直接拒绝，httpx 不被触达。"""
    import asyncio

    from app.services import llm_upstream

    called = {"http": False}

    class _NeverClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            called["http"] = True
            raise AssertionError("不应发起请求")

    with patch("httpx.AsyncClient", _NeverClient), pytest.raises(DomainError):
        asyncio.run(
            llm_upstream.post_chat_completions(
                base_url="http://10.0.0.5:8080/v1",
                api_key="sk",
                request_body={"model": "m", "messages": []},
                extra_headers={},
                timeout_seconds=10,
            )
        )
    assert called["http"] is False


def test_connectivity_test_returns_blocked_for_private() -> None:
    import asyncio

    from app.services.llm_test_service import test_openai_compatible

    outcome = asyncio.run(test_openai_compatible(base_url="http://10.0.0.5/v1", api_key="sk"))
    assert outcome.success is False
    assert outcome.reason_code == "blocked"


# ----------------------------------------------------------------------
# 流式预算
# ----------------------------------------------------------------------


def test_stream_budget_bytes_limit() -> None:
    from app.services.llm_upstream import _StreamBudget

    budget = _StreamBudget()
    with pytest.raises(DomainError):
        budget.charge("x" * (17 * 1024 * 1024))


def test_stream_budget_single_line_limit() -> None:
    """_iter_sse_data 对超长单行抛 too-large（防无换行恶意长行）。"""
    import asyncio

    from app.services.llm_upstream import _iter_sse_data, _StreamBudget

    class _FakeResponse:
        async def aiter_lines(self):
            yield "data: " + "x" * (2 * 1024 * 1024)

    async def run() -> None:
        async for _ in _iter_sse_data(_FakeResponse(), _StreamBudget()):  # type: ignore[arg-type]
            pass

    with pytest.raises(DomainError):
        asyncio.run(run())


def test_bad_sse_line_logged_and_skipped(caplog) -> None:
    """SSE 坏行跳过并记录告警（截断采样）。"""
    import asyncio
    import logging

    from app.services.llm_upstream import _iter_sse_data, _StreamBudget

    class _FakeResponse:
        async def aiter_lines(self):
            yield "data: not-json{"
            yield 'data: {"choices":[]}'

    async def run() -> list[Any]:
        return [c async for c in _iter_sse_data(_FakeResponse(), _StreamBudget())]  # type: ignore[arg-type]

    with caplog.at_level(logging.WARNING, logger="app.services.llm_upstream"):
        chunks = asyncio.run(run())
    assert chunks == [{"choices": []}]
    assert any("SSE" in record.message for record in caplog.records)


def test_stream_budget_normal_lines_pass() -> None:
    from app.services.llm_upstream import _StreamBudget

    budget = _StreamBudget()
    for _ in range(100):
        budget.charge('data: {"choices":[]}')
