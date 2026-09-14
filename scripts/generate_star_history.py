#!/usr/bin/env python3
"""从 GitHub API 抓取真实 star 时间线，渲染为自托管 SVG 图表。

背景：star-history.com 自 2026-08 起返回降级图表——纵轴被错误归一化为
0~1 小数区间、曲线退化成两点贝塞尔插值、无中间采样点。GitHub 已限制
第三方 star 数据访问，该站点静默返回 200 + 占位曲线而非真实时序。

本脚本直连 GitHub REST API：
  GET /repos/{owner}/{repo}/stargazers  （Accept: application/vnd.github.star+json）
响应带 starred_at 字段，取每页最后一条的累计数量即得真实时间线。

用法：
    uv run python scripts/generate_star_history.py
    uv run python scripts/generate_star_history.py --repo owner/name --out docs/assets/x.svg

无 token 时走匿名请求（每小时 60 次，足够）；有 GITHUB_TOKEN 环境变量时
自动使用，配额提升到 5000 次/小时。
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime

API_ROOT = "https://api.github.com"
PER_PAGE = 100


def _request(url: str) -> tuple[list[dict] | dict, dict[str, str]]:
    headers = {
        "Accept": "application/vnd.github.star+json",
        "User-Agent": "human-llm-gateway-star-history",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"GitHub API {exc.code} on {url}\n{body}") from exc


def fetch_star_timeline(repo: str) -> list[tuple[datetime, int]]:
    """返回 [(时间, 累计 star 数), ...]，按时间升序。"""
    owner_repo = repo
    meta, _ = _request(f"{API_ROOT}/repos/{owner_repo}")
    total = int(meta["stargazers_count"])
    print(f"仓库 {owner_repo}：当前 {total} star，开始抓取时间线……")

    if total == 0:
        now = datetime.now(UTC)
        return [(now, 0)]

    points: list[tuple[datetime, int]] = []
    page = 1
    while True:
        url = f"{API_ROOT}/repos/{owner_repo}/stargazers?per_page={PER_PAGE}&page={page}"
        batch, _headers = _request(url)
        assert isinstance(batch, list)
        if not batch:
            break
        for entry in batch:
            starred_at = entry.get("starred_at")
            if not starred_at:
                continue
            ts = datetime.fromisoformat(starred_at)
            points.append((ts, 0))  # 占位，稍后回填累计值
        # 页码由 Link 头控制更稳，这里用长度判断末页。
        if len(batch) < PER_PAGE:
            break
        page += 1
        if page > 100:  # 安全上限，1 万 star
            print("警告：达到分页上限 100 页，停止抓取。", file=sys.stderr)
            break

    points.sort(key=lambda item: item[0])
    timeline = [(ts, idx + 1) for idx, (ts, _) in enumerate(points)]
    print(f"抓取到 {len(timeline)} 个 star 事件。")
    return timeline


def render_svg(
    repo: str,
    timeline: list[tuple[datetime, int]],
    *,
    width: int = 800,
    height: int = 533,
) -> str:
    """渲染浅色风格的自托管 SVG 折线图（阶跃语义，真实时间轴）。"""
    left, top, right, bottom = 72.0, 64.0, 28.0, 60.0
    plot_w = width - left - right
    plot_h = height - top - bottom
    plot_left, plot_top = left, top
    plot_right, plot_bottom = left + plot_w, top + plot_h

    font = "system-ui, -apple-system, 'Segoe UI', 'PingFang SC', sans-serif"
    fg = "#1f2937"
    muted = "#6b7280"
    grid = "#e5e7eb"
    accent = "#e04a2f"
    bg = "#ffffff"

    t_min = timeline[0][0]
    t_max = timeline[-1][0]
    y_max = max(v for _, v in timeline)
    # 纵轴按真实整数域取值，上限向上取整到「好看的刻度」。
    y_top = _nice_ceiling(y_max)

    span = (t_max - t_min).total_seconds()
    if span <= 0:
        # 只有一个采样点：给一条水平短横线可读的最小宽度。
        t_min = t_min.replace(microsecond=0)
        t_max = t_min.replace(second=t_min.second) if False else t_min
        span = 1.0

    def x_of(ts: datetime) -> float:
        ratio = 0.0 if span <= 0 else (ts - t_min).total_seconds() / span
        return plot_left + ratio * plot_w

    def y_of(value: int) -> float:
        if y_top <= 0:
            return plot_bottom
        return plot_bottom - (value / y_top) * plot_h

    # 折线：按真实时间轴连接各 star 事件点（线性插值，非阶跃）。
    points = [(x_of(ts), y_of(v)) for ts, v in timeline]
    if len(points) == 1:
        x, y = points[0]
        line_path = f"M{x - 12:.2f},{y:.2f}H{x + 12:.2f}"
    else:
        segments = [f"M{points[0][0]:.2f},{points[0][1]:.2f}"]
        segments.extend(f"L{x:.2f},{y:.2f}" for x, y in points[1:])
        # 收尾水平段，让曲线延伸到右边界。
        segments.append(f"H{plot_right:.2f}")
        line_path = " ".join(segments)

    # 纵轴刻度：整数，最多 6 条。
    y_ticks: list[str] = []
    y_step = max(1, y_top // 5)
    value = 0
    while value <= y_top:
        y = y_of(value)
        y_ticks.append(
            f'<line x1="{plot_left:.2f}" y1="{y:.2f}" x2="{plot_right:.2f}" '
            f'y2="{y:.2f}" stroke="{grid}" stroke-width="1"/>'
        )
        y_ticks.append(
            f'<text x="{plot_left - 12:.2f}" y="{y + 4:.2f}" text-anchor="end" '
            f'font-family="{font}" font-size="12" fill="{muted}">{value}</text>'
        )
        value += y_step

    # 横轴刻度：按天数自适应，一个日期一个标签，绝不重叠。
    x_ticks: list[str] = []
    total_days = max(1.0, span / 86400)
    tick_targets = min(7, max(2, int(total_days // 3) + 1))
    seen_labels: set[str] = set()
    for idx in range(tick_targets):
        ratio = idx / (tick_targets - 1)
        ts = t_min + (t_max - t_min) * ratio
        label = ts.strftime("%b %d")
        if label in seen_labels:
            continue
        seen_labels.add(label)
        x = x_of(ts)
        anchor = "start" if idx == 0 else ("end" if idx == tick_targets - 1 else "middle")
        x_ticks.append(
            f'<line x1="{x:.2f}" y1="{plot_top:.2f}" x2="{x:.2f}" '
            f'y2="{plot_bottom:.2f}" stroke="{grid}" stroke-width="1"/>'
        )
        x_ticks.append(
            f'<text x="{x:.2f}" y="{plot_bottom + 22:.2f}" text-anchor="{anchor}" '
            f'font-family="{font}" font-size="12" fill="{muted}">{label}</text>'
        )

    latest_v = timeline[-1][1]
    latest_x = x_of(timeline[-1][0])

    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="{html.escape(f"{repo} star history")}">'
        ),
        f'<rect width="{width}" height="{height}" fill="{bg}"/>',
        (
            f'<text x="{plot_left:.2f}" y="34" font-family="{font}" font-size="18" '
            f'font-weight="600" fill="{fg}">Star History</text>'
        ),
        (
            f'<text x="{plot_left:.2f}" y="52" font-family="{font}" font-size="12" '
            f'fill="{muted}">{html.escape(repo)}</text>'
        ),
        *y_ticks,
        *x_ticks,
        (
            f'<path d="{line_path}" fill="none" stroke="{accent}" stroke-width="2" '
            f'stroke-linejoin="round"/>'
        ),
        f'<circle cx="{latest_x:.2f}" cy="{y_of(latest_v):.2f}" r="3.5" fill="{accent}"/>',
        (
            f'<text x="{plot_right:.2f}" y="{plot_top - 10:.2f}" text-anchor="end" '
            f'font-family="{font}" font-size="13" font-weight="600" fill="{fg}">'
            f"{latest_v} stars</text>"
        ),
        (
            f'<text x="{plot_left - 12:.2f}" y="{plot_top - 6:.2f}" '
            f'text-anchor="end" font-family="{font}" font-size="11" '
            f'transform="rotate(-90 {plot_left - 12:.2f} {plot_top - 6:.2f})" '
            f'fill="{muted}">GitHub Stars</text>'
        ),
        (
            f'<text x="{(plot_left + plot_right) / 2:.2f}" y="{height - 16:.2f}" '
            f'text-anchor="middle" font-family="{font}" font-size="12" '
            f'fill="{muted}">Date</text>'
        ),
        "</svg>",
    ]
    return "".join(parts)


def _nice_ceiling(value: int) -> int:
    """把纵轴上界向上取整到 1/2/5/10 的整数倍（至少 1）。"""
    if value <= 0:
        return 1
    if value <= 5:
        return value if value <= 4 else 5
    magnitude = 10 ** (len(str(value)) - 1)
    for factor in (1, 2, 5, 10):
        candidate = factor * magnitude
        if candidate >= value:
            return candidate
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="GuSheng107/human-llm-gateway")
    parser.add_argument("--out", default="docs/assets/star-history.svg")
    args = parser.parse_args()

    timeline = fetch_star_timeline(args.repo)
    svg = render_svg(args.repo, timeline)

    out_path = args.out
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(svg)
    print(f"已写入 {out_path}")


if __name__ == "__main__":
    main()
