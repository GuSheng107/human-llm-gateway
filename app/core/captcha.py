"""图形验证码：Pillow 生成，内存一次性存储，5 分钟过期。

并发安全：store 的清理/淘汰/写入/消费全部持锁（CPython dict 遍历期间
被并发 pop 会抛 RuntimeError）。
容量上限：_store 10,000 条、_generation_times 50,000 个 source，超出
按最旧优先淘汰，防长期运行内存膨胀。
防 OCR：4 位字符逐一随机旋转 + 干扰线/噪点。
"""

from __future__ import annotations

import base64
import io
import random
import secrets
import threading
import time

from PIL import Image, ImageDraw, ImageFont

# 去除易混淆字符（0/O、1/I/L、2/Z 等）。
_CAPTCHA_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ3456789"
_CAPTCHA_TTL = 300  # 秒

_store: dict[str, tuple[str, float]] = {}

# 内存 store 上限：惰性清理仍可能留下大量未消费条目，超出后按最旧优先淘汰。
_MAX_PENDING = 10_000
_RATE_WINDOW_SECONDS = 60.0
_MAX_GENERATIONS_PER_SOURCE = 30
# 频控字典容量上限（不同 source 数），超出按最旧 source 淘汰。
_MAX_RATE_SOURCES = 50_000
_generation_times: dict[str, list[float]] = {}
_lock = threading.Lock()


def _evict_oldest_source(table: dict[str, list[float]]) -> None:
    """按最近一次时间戳淘汰最旧的 source（调用方必须已持锁）。"""
    if not table:
        return
    oldest = min(table, key=lambda key: table[key][-1])
    table.pop(oldest, None)


def allow_captcha_request(source: str) -> bool:
    """限制单一来源的验证码生成频率，避免短时间内消耗过多 CPU。"""
    now = time.monotonic()
    with _lock:
        timestamps = [
            stamp
            for stamp in _generation_times.get(source, [])
            if now - stamp < _RATE_WINDOW_SECONDS
        ]
        if len(timestamps) >= _MAX_GENERATIONS_PER_SOURCE:
            _generation_times[source] = timestamps
            return False
        timestamps.append(now)
        _generation_times[source] = timestamps
        # 容量防御：不同 source 数超限时淘汰最旧条目。
        while len(_generation_times) > _MAX_RATE_SOURCES:
            _evict_oldest_source(_generation_times)
        return True


def _render_captcha(code: str) -> str:
    """渲染验证码图片：字符逐一旋转 + 干扰线/噪点，返回 data URL。"""
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
        # 每个字符单独画在透明小图上随机旋转后粘贴，抗 OCR。
        cell = Image.new("RGBA", (40, 44), (0, 0, 0, 0))
        cell_draw = ImageDraw.Draw(cell)
        color = (
            random.randint(30, 90),
            random.randint(100, 160),
            random.randint(200, 255),
            255,
        )
        cell_draw.text((4, 2), ch, font=font, fill=color)
        rotated = cell.rotate(random.uniform(-28, 28), resample=Image.BICUBIC, expand=True)
        x = 12 + index * 30 + random.randint(-3, 3)
        y = random.randint(0, 8)
        image.paste(rotated, (x, y), rotated)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def generate_captcha() -> tuple[str, str]:
    """生成验证码，返回 (token, data_url)。

    清理/淘汰/写入全程持锁：min() 遍历期间被其他线程 pop 会抛
    RuntimeError（verify_captcha 的消费路径与生成并发）。
    """
    code = "".join(secrets.choice(_CAPTCHA_CHARS) for _ in range(4))
    token = secrets.token_urlsafe(24)
    data_url = _render_captcha(code)  # 渲染在锁外（CPU 密集，持锁会放大竞争）

    now = time.time()
    with _lock:
        # 惰性清理过期条目，避免长期运行时内存字典持续增长。
        for stale_token in [
            t for t, (_c, created) in _store.items() if now - created > _CAPTCHA_TTL
        ]:
            _store.pop(stale_token, None)
        while len(_store) >= _MAX_PENDING:
            oldest = min(_store, key=lambda t: _store[t][1])
            _store.pop(oldest, None)
        _store[token] = (code, now)
    return token, data_url


def verify_captcha(token: str, code: str) -> bool:
    """一次性校验；无论通过与否都消费该 token（pop 持锁，与生成并发安全）。"""
    with _lock:
        entry = _store.pop(token, None)
    if entry is None:
        return False
    stored_code, created = entry
    if time.time() - created > _CAPTCHA_TTL:
        return False
    return stored_code == code.strip().upper()
