"""单进程 Prometheus 指标，标签只使用有限接口分类和状态码类别。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from starlette.types import ASGIApp, Message, Receive, Scope, Send


def _interface(path: str) -> str:
    protocols = {
        "/v1/chat/completions": "openai_chat",
        "/v1/responses": "openai_responses",
        "/v1/messages": "anthropic",
    }
    if path in protocols:
        return protocols[path]
    if path.startswith("/api/"):
        return "management"
    if path.startswith("/connectors/"):
        return "connector"
    return "other"


@dataclass
class MetricsState:
    requests: Counter[tuple[str, str]] = field(default_factory=Counter)
    inflight: Counter[str] = field(default_factory=Counter)

    def render(self) -> str:
        lines = [
            "# HELP hlg_http_requests_total Completed HTTP requests.",
            "# TYPE hlg_http_requests_total counter",
        ]
        for (interface, status), count in sorted(self.requests.items()):
            lines.append(
                f'hlg_http_requests_total{{interface="{interface}",status_class="{status}"}} {count}'
            )
        lines.extend(
            [
                "# HELP hlg_http_inflight_requests Active HTTP requests.",
                "# TYPE hlg_http_inflight_requests gauge",
            ]
        )
        for interface, count in sorted(self.inflight.items()):
            lines.append(f'hlg_http_inflight_requests{{interface="{interface}"}} {count}')
        return "\n".join(lines) + "\n"


class MetricsMiddleware:
    def __init__(self, app: ASGIApp, state: MetricsState) -> None:
        self.app = app
        self.state = state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in {"/metrics", "/healthz", "/readyz"}:
            await self.app(scope, receive, send)
            return
        interface = _interface(scope["path"])
        self.state.inflight[interface] += 1
        status = "5xx"

        async def counted_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                category = int(message["status"]) // 100
                status = f"{category}xx" if category in range(1, 6) else "5xx"
            await send(message)

        try:
            await self.app(scope, receive, counted_send)
        finally:
            self.state.inflight[interface] -= 1
            self.state.requests[interface, status] += 1
