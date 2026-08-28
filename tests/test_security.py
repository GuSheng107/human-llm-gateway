"""安全原语测试：Secret 加密契约、密码哈希、凭据哈希。"""

from __future__ import annotations

import base64
import secrets

import pytest

from app.core.exceptions import SecretCryptoError
from app.core.security import (
    decrypt_secret,
    encrypt_secret,
    generate_api_key,
    generate_session_token,
    generate_temporary_password,
    hash_password,
    hash_session_token,
    password_needs_rehash,
    verify_api_key,
    verify_password,
)
from app.domain.values import password_problems


@pytest.fixture()
def app_secret() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


class TestSecretEnvelope:
    def test_roundtrip(self, app_secret: str) -> None:
        envelope = encrypt_secret("super-secret", app_secret, "llm-secret")
        parts = envelope.split(".")
        assert parts[0] == "hlg1"
        assert parts[1] == "1"
        assert len(parts[2]) == 16  # 12 字节 nonce 的 base64url（无 padding）
        assert "=" not in envelope
        assert decrypt_secret(envelope, app_secret, "llm-secret") == "super-secret"

    def test_padded_envelope_rejected(self, app_secret: str) -> None:
        envelope = encrypt_secret("abcd", app_secret, "llm-secret")
        with pytest.raises(SecretCryptoError, match="无 padding"):
            decrypt_secret(envelope + "=", app_secret, "llm-secret")

    def test_cross_purpose_rejected(self, app_secret: str) -> None:
        envelope = encrypt_secret("token", app_secret, "llm-secret")
        with pytest.raises(SecretCryptoError):
            decrypt_secret(envelope, app_secret, "im-config")

    def test_wrong_key_rejected(self, app_secret: str) -> None:
        envelope = encrypt_secret("token", app_secret, "llm-secret")
        other = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
        with pytest.raises(SecretCryptoError):
            decrypt_secret(envelope, other, "llm-secret")

    def test_malformed_envelope_rejected(self, app_secret: str) -> None:
        with pytest.raises(SecretCryptoError):
            decrypt_secret("not-an-envelope", app_secret, "llm-secret")


class TestPasswordHash:
    def test_hash_and_verify(self) -> None:
        encoded = hash_password("correct-horse-battery-staple")
        assert encoded.startswith("$argon2id$")
        assert verify_password("correct-horse-battery-staple", encoded)
        assert not verify_password("wrong-password", encoded)

    def test_no_rehash_for_current_params(self) -> None:
        encoded = hash_password("correct-horse-battery-staple")
        assert password_needs_rehash(encoded) is False


class TestCredentialHash:
    def test_api_key(self) -> None:
        secret, _prefix, encoded = generate_api_key()
        assert secret.startswith("hlg_")
        assert verify_api_key(secret, encoded)
        assert not verify_api_key("hlg_wrong", encoded)

    def test_session_token_hash_is_deterministic(self) -> None:
        token, _prefix, encoded = generate_session_token()
        assert encoded == hash_session_token(token)


class TestTemporaryPassword:
    def test_generated_password_satisfies_policy(self) -> None:
        for _ in range(50):
            assert password_problems(generate_temporary_password()) == []
