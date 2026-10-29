"""DailyHotApi 聚合接口（可选）。

公开演示站 api-hot.imsyy.top 并非所有网络都能解析，因此默认关闭；
在 config/sources.yaml 里把 ``dailyhot.enabled`` 设为 true 并填可用的 ``base_url`` 即可启用。
自部署：``docker run -p 6688:6688 imsyy/dailyhot-api:latest``。

接口返回：``{code, name, title, type, total, data:[{title, desc, hot, url, mobileUrl, timestamp, author}]}``
"""

from urllib.parse import quote

from .base import clean_text, http_get_json, to_float

SOURCE_PREFIX = "DailyHot·"


def collect(base_url: str, platforms: list[str], limit_per_platform: int = 20) -> list[dict]:
    items: list[dict] = []
    root = base_url.rstrip("/")
    for platform in platforms:
        url = f"{root}/{quote(str(platform))}"
        try:
            data = http_get_json(url)
        except Exception:  # noqa: BLE001 单个平台失败不影响其他平台
            continue
        source = SOURCE_PREFIX + (clean_text((data or {}).get("name")) or clean_text(platform))
        rows = (data or {}).get("data") or []
        for rank, row in enumerate(rows[:limit_per_platform], start=1):
            title = clean_text(row.get("title"))
            if not title:
                continue
            items.append(
                {
                    "title": title,
                    "description": clean_text(row.get("desc"))[:160],
                    "source": source,
                    "source_url": clean_text(row.get("url"))
                    or clean_text(row.get("mobileUrl")),
                    "raw_hot": to_float(row.get("hot")),
                    "rank": rank,
                }
            )
    return items
