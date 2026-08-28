"""图形验证码单元测试。"""

from __future__ import annotations

from app.core import captcha


def test_generate_returns_png_data_url() -> None:
    token, image = captcha.generate_captcha()
    assert token
    assert image.startswith("data:image/png;base64,")
    stored_code = captcha._store[token][0]
    assert len(stored_code) == 4


def test_verify_case_insensitive_and_one_shot() -> None:
    token, _image = captcha.generate_captcha()
    stored_code = captcha._store[token][0]
    assert captcha.verify_captcha(token, stored_code.lower()) is True
    # 一次性：同一个 token 第二次校验必然失败
    assert captcha.verify_captcha(token, stored_code) is False


def test_verify_unknown_or_wrong() -> None:
    assert captcha.verify_captcha("no-such-token", "ABCD") is False
    token, _image = captcha.generate_captcha()
    stored_code = captcha._store[token][0]
    wrong = "XXXX" if stored_code != "XXXX" else "YYYY"
    assert captcha.verify_captcha(token, wrong) is False
