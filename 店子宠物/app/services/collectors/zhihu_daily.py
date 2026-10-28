"""知乎日报：官方公开 JSON 接口（每日精选，无热度值，按名次折算热度）。"""

from .base import clean_text, http_get_json

SOURCE = "知乎日报"
URL = "https://news-at.zhihu.com/api/4/news/latest"


def collect(limit: int = 20) -> list[dict]:
    data = http_get_json(URL)
    rows = (data or {}).get("stories") or []
    # 头条故事也算每日精选的一部分，合并去重后一起返回
    rows = rows + list((data or {}).get("top_stories") or [])
    items: list[dict] = []
    seen: set[str] = set()
    for rank, row in enumerate(rows, start=1):
        title = clean_text(row.get("title"))
        if not title or title in seen:
            continue
        seen.add(title)
        story_id = row.get("id")
        url = clean_text(row.get("url")) or (
            f"https://daily.zhihu.com/story/{story_id}" if story_id else ""
        )
        items.append(
            {
                "title": title,
                "description": clean_text(row.get("hint")),
                "source": SOURCE,
                "source_url": url,
                "raw_hot": None,
                "rank": rank,
            }
        )
        if len(items) >= limit:
            break
    return items
