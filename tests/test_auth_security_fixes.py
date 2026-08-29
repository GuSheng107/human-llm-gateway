"""认证安全修复验证：captcha 并发/容量、节流组合 key、注册节流。

1. generate_captcha / verify_captcha 并发无 RuntimeError（原 min() 遍历
   与 pop 竞态）。
2. _store / _generation_times / _failures 容量上限淘汰。
3. 登录节流按 ip|username 组合：同 IP 不同用户不互相误锁（反代聚合 DoS）。
4. 注册失败（验证码错 / 邀请码错）计数节流。
"""

from __future__ import annotations

import threading

from app.core import login_throttle
from app.core.captcha import _store, allow_captcha_request, generate_captcha, verify_captcha

# ----------------------------------------------------------------------
# #1 并发安全
# ----------------------------------------------------------------------


def test_captcha_concurrent_generate_and_verify_no_error() -> None:
    """并发生成 + 消费：不抛 RuntimeError（dict 遍历/修改竞态）。"""
    errors: list[Exception] = []
    tokens: list[str] = []
    token_lock = threading.Lock()

    def generator() -> None:
        try:
            for _ in range(50):
                token, _ = generate_captcha()
                with token_lock:
                    tokens.append(token)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def verifier() -> None:
        try:
            for _ in range(50):
                with token_lock:
                    if tokens:
                        verify_captcha(tokens.pop(), "XXXX")
                # 同时消费不存在的 token（pop 路径）
                verify_captcha("nonexistent-token", "XXXX")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=generator) for _ in range(4)]
    threads += [threading.Thread(target=verifier) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [], errors


def test_captcha_verify_correct_code_roundtrip() -> None:
    """渲染重构后基本往返仍可用（写入 store 的 code 与 verify 一致）。"""
    token, _image = generate_captcha()
    code = _store[token][0]
    assert verify_captcha(token, code.lower()) is True  # 大小写不敏感
    assert verify_captcha(token, code) is False  # 一次性


def test_captcha_image_is_rotated_png() -> None:
    """渲染产出合法 PNG data URL（旋转管线不破坏输出）。"""
    import base64
    import io

    from PIL import Image

    _token, image = generate_captcha()
    assert image.startswith("data:image/png;base64,")
    raw = base64.b64decode(image.split(",", 1)[1])
    pil = Image.open(io.BytesIO(raw))
    assert pil.format == "PNG"
    assert pil.size == (150, 50)


# ----------------------------------------------------------------------
# #3 容量上限
# ----------------------------------------------------------------------


def test_captcha_store_capacity_eviction() -> None:
    """_store 超 _MAX_PENDING 后按最旧淘汰，不无限增长。"""
    from app.core.captcha import _MAX_PENDING

    _store.clear()
    # 直接灌满底层 store（绕过渲染，仅验证容量逻辑）
    for i in range(_MAX_PENDING + 100):
        _store[f"tok-{i}"] = ("AAAA", float(i))
    generate_captcha()  # 触发清理路径（含 while 淘汰）
    assert len(_store) <= _MAX_PENDING


def test_generation_times_capacity_eviction() -> None:
    """_generation_times 超 _MAX_RATE_SOURCES 后淘汰最旧 source。"""
    from app.core.captcha import _MAX_RATE_SOURCES, _generation_times

    _generation_times.clear()
    for i in range(_MAX_RATE_SOURCES + 50):
        _generation_times[f"src-{i}"] = [float(i)]
    assert allow_captcha_request("fresh-source") is True
    assert len(_generation_times) <= _MAX_RATE_SOURCES


def test_login_throttle_capacity_eviction() -> None:
    """_failures 超 _MAX_TRACKED_KEYS 后淘汰最旧 key。"""
    from app.core.login_throttle import _MAX_TRACKED_KEYS, _failures

    _failures.clear()
    for i in range(_MAX_TRACKED_KEYS + 50):
        _failures[f"key-{i}"] = [float(i)]
    login_throttle.record_failure("fresh-key")
    assert len(_failures) <= _MAX_TRACKED_KEYS


# ----------------------------------------------------------------------
# #2 组合 key：反代聚合不误锁
# ----------------------------------------------------------------------


def test_throttle_key_combines_ip_and_username() -> None:
    from app.api.auth import _throttle_key

    class _Client:
        host = "10.0.0.1"

    class _Request:
        client = _Client()

    key_a = _throttle_key(_Request(), "Alice")  # type: ignore[arg-type]
    key_b = _throttle_key(_Request(), "Bob")  # type: ignore[arg-type]
    assert key_a == "10.0.0.1|alice"
    assert key_b == "10.0.0.1|bob"
    assert key_a != key_b


def test_same_ip_different_users_not_locked_together() -> None:
    """同一 IP（如反代）下：用户 A 触发锁定不影响用户 B。"""
    from app.core import login_throttle as lt

    lt._failures.clear()
    for _ in range(10):
        lt.record_failure("1.2.3.4|alice")
    assert lt.allow("1.2.3.4|alice") is False
    assert lt.allow("1.2.3.4|bob") is True


# ----------------------------------------------------------------------
# #5 注册节流
# ----------------------------------------------------------------------


def test_register_captcha_failure_counted_and_throttled(client, monkeypatch) -> None:
    """注册验证码错误也计数：超限后返回 429（还原真实校验，绕过 conftest mock）。"""
    from app.api import auth as auth_module
    from app.core.captcha import verify_captcha as real_verify

    monkeypatch.setattr(auth_module, "verify_captcha", real_verify)
    from app.core import login_throttle as lt

    lt._failures.clear()
    throttled = False
    for _ in range(12):
        resp = client.post(
            "/api/auth/register",
            json={
                "invitation_code": "IRRELEVANT",
                "username": "throttle-probe-user",
                "display_name": "probe",
                "password": "Some-Pass1!",
                "captcha_token": "bad-token",
                "captcha_code": "AAAA",
            },
        )
        if resp.status_code == 429:
            throttled = True
            break
        assert resp.status_code == 400  # 验证码错误
    assert throttled, "连续失败后应被 429 节流"


def test_register_invitation_failure_counted(client) -> None:
    """验证码正确但邀请码错误：同样计数（不误伤验证码正确的其他用户）。"""
    token, _image = generate_captcha()
    code = _store[token][0]
    resp = client.post(
        "/api/auth/register",
        json={
            "invitation_code": "WRONG-INVITE",
            "username": "invite-probe-user",
            "display_name": "probe",
            "password": "Some-Pass1!",
            "captcha_token": token,
            "captcha_code": code,
        },
    )
    assert resp.status_code == 400
    from app.core import login_throttle as lt

    assert any("invite-probe-user" in key for key in lt._failures)


def test_login_failure_key_includes_username(client) -> None:
    """登录失败（用户不存在）按 ip|username 组合计数（conftest 绕过验证码）。"""
    from app.core import login_throttle as lt

    lt._failures.clear()
    resp = client.post(
        "/api/auth/login",
        json={
            "username": "no-such-user",
            "password": "Whatever-1!",
            "captcha_token": "t",
            "captcha_code": "c",
        },
    )
    assert resp.status_code == 401
    assert any(key.endswith("|no-such-user") for key in lt._failures)
