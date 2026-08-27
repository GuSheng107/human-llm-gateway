import base64
import hashlib
import hmac
import secrets
import time

from cryptography.fernet import Fernet


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, salt_text, digest_text = encoded.split("$", 2)
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(digest_text.encode())
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def issue_admin_token(username: str, secret: str, ttl_seconds: int = 8 * 3600) -> str:
    expires = int(time.time()) + ttl_seconds
    payload = f"{username}.{expires}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def verify_admin_token(token: str, secret: str) -> str | None:
    try:
        username, expires_text, signature = token.split(".", 2)
        payload = f"{username}.{expires_text}"
        if int(expires_text) < int(time.time()):
            return None
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return username if hmac.compare_digest(signature, expected) else None
    except (ValueError, TypeError):
        return None


def generate_api_key() -> tuple[str, str, str]:
    secret = f"hlg_{secrets.token_urlsafe(32)}"
    prefix = secret[:12]
    salt = secrets.token_hex(16)
    digest = hashlib.sha256(f"{salt}:{secret}".encode()).hexdigest()
    return secret, prefix, f"sha256${salt}${digest}"


def verify_api_key(secret: str, encoded: str) -> bool:
    try:
        _, salt, expected = encoded.split("$", 2)
        actual = hashlib.sha256(f"{salt}:{secret}".encode()).hexdigest()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _fernet(app_secret: str) -> Fernet:
    digest = hashlib.sha256(app_secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str, app_secret: str) -> str:
    return _fernet(app_secret).encrypt(value.encode()).decode()


def decrypt_secret(value: str, app_secret: str) -> str:
    return _fernet(app_secret).decrypt(value.encode()).decode()

