"""SSRF 防护：LLM 上游 base_url 目标地址分档校验。

分两档（docs/API_CONTRACT.md §6 部署边界）：

1. 无条件拒绝（无开关，合法自建场景没有任何理由访问）：
   - 云元数据：169.254.169.254（AWS/GCP/阿里云/腾讯云等 IMDS）、
     100.100.100.200（阿里云）、168.63.129.16（Azure WireServer）
   - 链路本地 169.254.0.0/16 全段
   - 0.0.0.0/8（未指定源）与 IPv4 广播 255.255.255.255
2. 默认拒绝、显式开关放行（自建网关连本机 Ollama/内网 vLLM 属正当用法）：
   - 回环 127.0.0.0/8、IPv6 ::1
   - RFC1918 私有段（10/8、172.16/12、192.168/16）
   - IPv6 ULA fc00::/7、链路本地 fe80::/10
   - 不可全局路由的保留段（192.0.0.0/24、192.0.2.0/24、198.51.100.0/24、
     203.0.113.0/24、240.0.0.0/4）
   开关：LLM_ALLOW_PRIVATE_UPSTREAM=true（默认 false）。

校验时机（防御 DNS rebinding）：
- 配置创建/更新时：getaddrinfo 全量解析结果逐 IP 校验（fail-closed，
  任一解析失败即拒绝）；
- 每次上游请求前：重新解析（配置后 DNS 可能改变指向）再校验。

域名解析为同步 getaddrinfo（配置校验在同步服务层；请求前校验经
run_in_threadpool 包装的调用链执行，不阻塞事件循环）。
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from .config import get_settings
from .constants import (
    SSRF_METADATA_HOSTS,
    SSRF_METADATA_NETWORKS,
)
from .logging import log_event

_CLOUD_METADATA_HOSTS: frozenset[str] = SSRF_METADATA_HOSTS
_METADATA_V4_NETWORKS = tuple(ipaddress.ip_network(net) for net in SSRF_METADATA_NETWORKS)

_PRIVATE_V4_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "10.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "0.0.0.0/8",
        "255.255.255.255/32",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "240.0.0.0/4",
    )
)
_PRIVATE_V6_NETWORKS = tuple(
    ipaddress.ip_network(net) for net in ("::1/128", "fc00::/7", "fe80::/10")
)


class SsrfViolation(Exception):
    """目标地址违反分档策略；message 面向用户（不含内部细节）。"""


def _classify_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """返回违规类别；None 表示地址合法。"""
    text = str(ip)
    # 云元数据主机名精确匹配（部分供应商元数据域名不走特殊网段）。
    networks = _METADATA_V4_NETWORKS if ip.version == 4 else ()
    for net in networks:
        if ip in net:
            return "cloud-metadata"
    if text in _CLOUD_METADATA_HOSTS:
        return "cloud-metadata"
    all_private = _PRIVATE_V4_NETWORKS if ip.version == 4 else _PRIVATE_V6_NETWORKS
    for net in all_private:
        if ip in net:
            # 169.254/16 已在元数据档；其余私有/保留段归 private 档。
            if ip.version == 4 and ip in _METADATA_V4_NETWORKS[0]:
                return "cloud-metadata"
            return "private"
    return None


def validate_host_ips(host: str) -> None:
    """解析 host 并逐 IP 分档校验；违规抛 SsrfViolation（fail-closed）。

    解析失败同样拒绝：DNS rebinding 场景下"暂时解析不出"与"解析出
    内网地址"同等对待。
    """
    allow_private = get_settings().llm_allow_private_upstream
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        log_event("warning", "ssrf.dns_resolve_failed", "上游主机名解析失败", host=host)
        raise SsrfViolation("无法解析上游主机地址，请检查 base_url") from exc
    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        ip_text = sockaddr[0]
        if ip_text in seen:
            continue
        seen.add(ip_text)
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError as exc:
            raise SsrfViolation("上游主机地址非法") from exc
        category = _classify_ip(ip)
        if category == "cloud-metadata":
            log_event("warning", "ssrf.blocked", "云元数据地址被拒绝", host=host)
            raise SsrfViolation("不允许访问云元数据或链路本地地址")
        if category == "private" and not allow_private:
            log_event("warning", "ssrf.blocked", "私有网段被拒绝", host=host)
            raise SsrfViolation(
                "不允许访问私有/回环地址；自建内网上游需设置 LLM_ALLOW_PRIVATE_UPSTREAM=true"
            )


def validate_base_url(base_url: str) -> None:
    """解析 base_url 的 host 并执行 SSRF 分档校验。"""
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").strip("[]")
    if not host:
        raise SsrfViolation("base_url 缺少主机地址")
    # 已知云元数据主机名（如 metadata.google.internal）按名拒绝。
    if host.lower() in {h.lower() for h in _CLOUD_METADATA_HOSTS}:
        raise SsrfViolation("不允许访问云元数据或链路本地地址")
    # 字面 IP 直接分档，无需 DNS。
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        validate_host_ips(host)
        return
    category = _classify_ip(ip)
    if category == "cloud-metadata":
        raise SsrfViolation("不允许访问云元数据或链路本地地址")
    if category == "private" and not get_settings().llm_allow_private_upstream:
        raise SsrfViolation(
            "不允许访问私有/回环地址；自建内网上游需设置 LLM_ALLOW_PRIVATE_UPSTREAM=true"
        )
