"""热点采集适配层：把多个公开数据源统一成同一份结构。

设计要点：
- 每个源一个模块，返回 ``List[dict]``（字段见 base.py）；
- 单个源失败只记日志，不影响其它源；
- 是否启用、采集条数写在 ``config/sources.yaml``，改配置不用改代码。
"""

from __future__ import annotations

import logging

from ...config import load_yaml
from . import bilibili, dailyhot, toutiao, zhihu_daily
from .base import CollectorError, clean_text  # noqa: F401

logger = logging.getLogger(__name__)

# 直接调用型 collector：key -> 函数
SIMPLE_COLLECTORS = {
    "toutiao": toutiao.collect,
    "bilibili": bilibili.collect,
    "zhihu_daily": zhihu_daily.collect,
}


def enabled_sources() -> dict:
    """读取 config/sources.yaml，返回 {key: spec}（只含 enabled=true 的源）。"""
    try:
        cfg = load_yaml("sources.yaml") or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 sources.yaml 失败：%s", exc)
        return {}
    sources = cfg.get("sources") or {}
    return {k: v for k, v in sources.items() if (v or {}).get("enabled")}


def category_keywords() -> dict:
    """板块分类词表：{板块: [关键词, ...]}。"""
    try:
        cfg = load_yaml("sources.yaml") or {}
        return cfg.get("category_keywords") or {}
    except Exception:  # noqa: BLE001
        return {}


def source_category_map() -> dict:
    """来源自带分类（如 B 站分区 tname）到板块的映射，关键词没命中时兜底。"""
    try:
        cfg = load_yaml("sources.yaml") or {}
        return cfg.get("source_category_map") or {}
    except Exception:  # noqa: BLE001
        return {}


def collect_all() -> list[dict]:
    """依次采集所有启用的源，返回合并后的原始条目。"""
    items: list[dict] = []
    for key, spec in enabled_sources().items():
        spec = spec or {}
        limit = int(spec.get("limit", 30))
        try:
            if key == "dailyhot":
                base_url = clean_text(spec.get("base_url"))
                platforms = spec.get("platforms") or ["bilibili"]
                if base_url:
                    items.extend(dailyhot.collect(base_url, platforms, limit))
                else:
                    logger.warning("dailyhot 已启用但未配置 base_url，跳过")
            elif key == "bilibili_parts":
                # 分区排行榜：每个分区单独成源，保证游戏 / 美食 / 美妆等板块有真实内容
                for part in spec.get("parts") or []:
                    try:
                        items.extend(bilibili.collect_part(part, limit))
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("B站分区榜 %s 采集失败：%s", part.get("label"), exc)
            else:
                fn = SIMPLE_COLLECTORS.get(key)
                if fn is None:
                    logger.warning("未知热点源 %s，跳过", key)
                    continue
                items.extend(fn(limit))
        except CollectorError as exc:
            logger.warning("热点源 %s 采集失败：%s", key, exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("热点源 %s 异常：%s", key, exc)
    return items
