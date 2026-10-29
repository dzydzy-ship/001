"""热点采集适配层的公共设施。

统一约定：每个 collector 返回 ``list[dict]``，每项字段：

    title       必填，热点标题
    description 可选，摘要 / 补充说明
    source      来源名称，例如 "今日头条热榜"
    source_url  原文链接
    raw_hot     源站原始热度数值（None 表示该源没有热度值）
    rank        在源列表中的名次（从 1 开始）

category / keywords / hot_score 由 ``app/services/hotspots.py`` 统一归一化后写入，
collector 自身不负责这些字段，保证不同源的数据口径一致。
"""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}


class CollectorError(RuntimeError):
    """采集失败（网络异常、被风控、响应格式不符）。"""


def http_get_json(url: str, timeout: float = 15.0, headers: dict | None = None) -> Any:
    """GET 并解析 JSON；任何异常统一抛 CollectorError，由上层逐个源吞掉。"""
    merged = {**DEFAULT_HEADERS, **(headers or {})}
    try:
        with httpx.Client(timeout=timeout, headers=merged, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:  # noqa: BLE001
        raise CollectorError(f"{url} -> {type(exc).__name__}: {exc}") from exc


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\u3000", " ").strip()


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
