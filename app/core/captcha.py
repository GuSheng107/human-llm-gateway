"""图形验证码：Pillow 生成，内存一次性存储，5 分钟过期。"""

from __future__ import annotations

import base64
import io
import random
import secrets
import time

from PIL import Image, ImageDraw, ImageFont

# 去除易混淆字符（0/O、1/I/L、2/Z 等）。
_CAPTCHA_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ3456789"
_CAPTCHA_TTL = 300  # 秒

_store: dict[str, tuple[str, float]] = {}


def generate_captcha() -> tuple[str, str]:
    """生成验证码，返回 (token, data_url)。"""
    # 惰性清理过期条目，避免长期运行时内存字典持续增长。
    now = time.time()
    for stale_token, (_code, created) in tuple(_store.items()):
        if now - created > _CAPTCHA_TTL:
            _store.pop(stale_token, None)
    code = "".join(secrets.choice(_CAPTCHA_CHARS) for _ in range(4))
    token = secrets.token_urlsafe(24)
    _store[token] = (code, now)

    width, height = 150, 50
    image = Image.new("RGB", (width, height), (246, 249, 255))
    draw = ImageDraw.Draw(image)

    for _ in range(6):
        draw.line(
            [
                (random.randint(0, width), random.randint(0, height)),
                (random.randint(0, width), random.randint(0, height)),
            ],
            fill=(random.randint(170, 210), random.randint(170, 210), random.randint(190, 230)),
            width=1,
        )
    for _ in range(120):
        draw.point(
            (random.randint(0, width - 1), random.randint(0, height - 1)),
            fill=(random.randint(190, 220), random.randint(190, 220), random.randint(210, 240)),
        )

    font = ImageFont.load_default(size=32)
    for index, ch in enumerate(code):
        x = 18 + index * 30 + random.randint(-4, 4)
        y = random.randint(4, 12)
        color = (
            random.randint(30, 90),
            random.randint(100, 160),
            random.randint(200, 255),
        )
        draw.text((x, y), ch, font=font, fill=color)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    return token, data_url


def verify_captcha(token: str, code: str) -> bool:
    """一次性校验；无论通过与否都消费该 token。"""
    entry = _store.pop(token, None)
    if entry is None:
        return False
    stored_code, created = entry
    if time.time() - created > _CAPTCHA_TTL:
        return False
    return stored_code == code.strip().upper()
