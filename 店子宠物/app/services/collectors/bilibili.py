"""B 站排行榜：官方公开 JSON 接口（全站榜 + 分区榜）。"""

from .base import clean_text, http_get_json, to_float

SOURCE = "B站排行榜"
URL = "https://api.bilibili.com/x/web-interface/ranking/v2?rid=0&type=all"
PART_URL = "https://api.bilibili.com/x/web-interface/ranking/v2?rid={rid}&type=all"


def _rows(rid: int | str) -> list[dict]:
    data = http_get_json(PART_URL.format(rid=rid))
    return ((data or {}).get("data") or {}).get("list") or []


def _to_items(rows: list[dict], source: str, category: str, limit: int) -> list[dict]:
    items: list[dict] = []
    for rank, row in enumerate(rows[:limit], start=1):
        title = clean_text(row.get("title"))
        if not title:
            continue
        bvid = clean_text(row.get("bvid"))
        url = (
            f"https://www.bilibili.com/video/{bvid}"
            if bvid
            else clean_text(row.get("short_link_v2"))
        )
        desc = clean_text(row.get("desc"))
        items.append(
            {
                "title": title,
                "description": desc.splitlines()[0][:160] if desc else "",
                "source": source,
                "source_url": url,
                "raw_hot": to_float((row.get("stat") or {}).get("view")),
                "rank": rank,
                "hint_category": category or clean_text(row.get("tname")),
            }
        )
    return items


def collect(limit: int = 30) -> list[dict]:
    """全站排行榜。"""
    return _to_items(_rows(0), SOURCE, "", limit)


def collect_part(part: dict, limit: int = 12) -> list[dict]:
    """分区排行榜：part = {rid, label, category}，用来保证游戏/美食/美妆等板块有真实内容。"""
    rid = part.get("rid")
    label = clean_text(part.get("label")) or str(rid)
    category = clean_text(part.get("category"))
    rows = _rows(rid)
    return _to_items(rows, f"B站·{label}榜", category, limit)
