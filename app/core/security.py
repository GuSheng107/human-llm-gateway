"""安全原语：Argon2id 密码哈希、Secret 加密契约、凭据哈希。

密码哈希固定 Argon2id（m=19456 KiB、t=2、p=1，PHC 编码字符串）；
Secret 加密固定 HKDF-SHA256 派生 + AES-256-GCM + 96-bit 随机 nonce +
按用途绑定的 AAD + 文本 envelope（见 docs/DATABASE.md §2.4）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .constants import SECRET_ENVELOPE_PREFIX, SECRET_HKDF_INFO, SECRET_KEY_VERSION
from .exceptions import SecretCryptoError

# Argon2id 基线参数（memory 单位为 KiB）。
ARGON2_MEMORY_KIB = 19456
ARGON2_ITERATIONS = 2
ARGON2_PARALLELISM = 1

_password_hasher = PasswordHasher(
    memory_cost=ARGON2_MEMORY_KIB,
    time_cost=ARGON2_ITERATIONS,
    parallelism=ARGON2_PARALLELISM,
)


# ---------------------------------------------------------------------------
# 密码哈希
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return _password_hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(encoded: str) -> bool:
    """存储参数低于当前策略时返回 True（用于登录成功时同流程重哈希）。"""
    try:
        return _password_hasher.check_needs_rehash(encoded)
    except (InvalidHashError, ValueError):
        return True


# ---------------------------------------------------------------------------
# Secret 加密契约
# ---------------------------------------------------------------------------


def _derive_key(app_secret: str) -> bytes:
    raw = _decode_app_secret(app_secret)
    hkdf = HKDF(algorithm=SHA256(), length=32, salt=None, info=SECRET_HKDF_INFO)
    return hkdf.derive(raw)


def _encode_b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _decode_b64url(value: str, *, label: str) -> bytes:
    """严格解析无 padding 的规范 RFC 4648 URL-safe Base64。"""
    if not value or "=" in value:
        raise SecretCryptoError(f"{label} 不是无 padding 的规范 base64url")
    padding = "=" * (-len(value) % 4)
    try:
        raw = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise SecretCryptoError(f"{label} 不是合法 base64url") from exc
    if _encode_b64url(raw) != value:
        raise SecretCryptoError(f"{label} 不是规范 base64url")
    return raw


def _decode_app_secret(app_secret: str) -> bytes:
    raw = _decode_b64url(app_secret, label="APP_SECRET")
    if len(raw) != 32:
        raise SecretCryptoError("APP_SECRET 必须解码为 32 字节")
    return raw


def _aad(purpose: str) -> bytes:
    return f"human-llm-gateway/{purpose}/v1".encode()


def encrypt_secret(plaintext: str, app_secret: str, purpose: str) -> str:
    """加密为 `hlg1.<key_version>.<nonce_b64url>.<ciphertext_and_tag_b64url>`。"""
    key = _derive_key(app_secret)
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode(), _aad(purpose))
    nonce_b64 = _encode_b64url(nonce)
    ciphertext_b64 = _encode_b64url(ciphertext)
    return f"{SECRET_ENVELOPE_PREFIX}.{SECRET_KEY_VERSION}.{nonce_b64}.{ciphertext_b64}"


def decrypt_secret(envelope: str, app_secret: str, purpose: str) -> str:
    parts = envelope.split(".")
    if len(parts) != 4 or parts[0] != SECRET_ENVELOPE_PREFIX:
        raise SecretCryptoError("Secret envelope 结构非法")
    _, key_version, nonce_b64, ciphertext_b64 = parts
    if key_version != str(SECRET_KEY_VERSION):
        raise SecretCryptoError(f"未知的 Secret key_version: {key_version}")
    nonce = _decode_b64url(nonce_b64, label="Secret nonce")
    ciphertext = _decode_b64url(ciphertext_b64, label="Secret ciphertext")
    if len(nonce) != 12:
        raise SecretCryptoError("Secret nonce 长度不是 12 字节")
    if len(ciphertext) < 16:
        raise SecretCryptoError("Secret ciphertext 缺少 AES-GCM tag")
    key = _derive_key(app_secret)
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, _aad(purpose)).decode()
    except Exception as exc:
        raise SecretCryptoError("Secret 解密失败（密钥或用途不匹配）") from exc


# ---------------------------------------------------------------------------
# 凭据哈希（邀请码、API Key、会话 token、绑定码）
# ---------------------------------------------------------------------------


def _salted_digest(secret: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.sha256(f"{salt}:{secret}".encode()).hexdigest()
    return f"sha256${salt}${digest}"


def _verify_salted_digest(secret: str, encoded: str) -> bool:
    try:
        _, salt, expected = encoded.split("$", 2)
    except ValueError:
        return False
    actual = hashlib.sha256(f"{salt}:{secret}".encode()).hexdigest()
    return hmac.compare_digest(actual, expected)


def generate_api_key() -> tuple[str, str, str]:
    """返回 (明文, 前缀, 哈希)。明文只在创建响应展示一次。"""
    secret = f"hlg_{secrets.token_urlsafe(32)}"
    prefix = secret[:12]
    return secret, prefix, _salted_digest(secret)


def verify_api_key(secret: str, encoded: str) -> bool:
    return _verify_salted_digest(secret, encoded)


def generate_invitation_code(length: int = 16) -> tuple[str, str, str]:
    """返回 (明文邀请码, 前缀, 哈希)。"""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    code = "".join(secrets.choice(alphabet) for _ in range(length))
    return code, code[:8], _salted_digest(code)


def verify_invitation_code(code: str, encoded: str) -> bool:
    return _verify_salted_digest(code.upper().strip(), encoded)


def generate_session_token() -> tuple[str, str, str]:
    """返回 (明文 token, 前缀, 哈希)。会话鉴权凭 token 反查 token_hash。

    会话 token 是 256-bit 高熵随机值，因此用确定性 SHA256 哈希即可安全存储，
    无需加盐（加盐会失去按明文反查的能力）。
    """
    token = secrets.token_urlsafe(32)
    return token, token[:12], hash_session_token(token)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_binding_code(length: int = 8) -> tuple[str, str]:
    """一次性绑定码，返回 (明文, 哈希)。"""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    code = "".join(secrets.choice(alphabet) for _ in range(length))
    return code, _salted_digest(code.upper().strip())


def verify_binding_code(code: str, encoded: str) -> bool:
    return _verify_salted_digest(code.upper().strip(), encoded)


def generate_temporary_password() -> str:
    """生成满足当前长度与 blocklist 策略的一次性临时密码。"""
    return secrets.token_urlsafe(18)
