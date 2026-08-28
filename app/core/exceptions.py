"""基础设施异常。

业务领域的稳定错误码在 domain.errors；这里只放基础设施层的异常。
"""

from __future__ import annotations


class CoreError(Exception):
    """基础设施异常基类。"""


class SecretCryptoError(CoreError):
    """Secret 加密/解密失败（格式非法、key_version 不匹配、认证失败等）。"""


class SchemaVersionMismatch(CoreError):
    """数据库 schema_version 与代码不一致。"""
