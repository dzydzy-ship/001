"""今日头条热榜：官方公开 JSON 接口，无需登录。"""

from .base import clean_text, http_get_json, to_float

SOURCE = "今日头条热榜"
URL = "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc"


def collect(limit: int = 30) -> list[dict]:
    data = http_get_json(URL)
    rows = (data or {}).get("data") or []
    items: list[dict] = []
    for rank, row in enumerate(rows[:limit], start=1):
        title = clean_text(row.get("Title"))
        if not title:
            continue
        items.append(
            {
                "title": title,
                "description": clean_text(row.get("Label")),
                "source": SOURCE,
                "source_url": clean_text(row.get("Url")),
                "raw_hot": to_float(row.get("HotValue")),
                "rank": rank,
            }
        )
    return items
