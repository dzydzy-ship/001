"""模块二：热点数据采集与热度时间序列维护。

数据来源分两类：

1. 真实采集：``app/services/collectors/`` 下的各公开数据源（头条热榜 / B站排行榜 /
   知乎日报 / 可选的 DailyHotApi），在 ``config/sources.yaml`` 里开关；采集失败只记日志，
   不影响其它源。
2. 演示兜底：当所有真实源都不可用、且库里还没有任何热点时，写入内置的 DEMO_HOTSPOTS，
   保证离线 / 断网环境也能演示。

入库前统一做四件事：板块分类（关键词词表）、关键词抽取、源内热度归一化（0~100）、
去重（同一来源下标题相同视为同一条，只更新热度与快照）。
"""

import logging
import math
import threading
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Hotspot
from .collectors import category_keywords, collect_all, source_category_map
from .collectors.base import clean_text

logger = logging.getLogger(__name__)

# 采集互斥锁：定时任务（APScheduler 后台线程）与手动「立即采集」（请求线程）可能同时触发，
# 并发采集既浪费网络请求，也可能造成 SQLite 写锁竞争。串行化后第二次请求直接返回「进行中」。
_COLLECT_LOCK = threading.Lock()

# 演示热点（真实源全部不可用且库为空时兜底），覆盖户外/健康/数码/美妆/游戏/食品/汽车/宠物/家居/运动健身/母婴亲子等板块
DEMO_HOTSPOTS = [
    {
        "title": "夏季户外活动热度上升",
        "description": "气温升高，露营、徒步、骑行等户外活动讨论量显著增长。",
        "category": "户外",
        "keywords": ["户外", "露营", "徒步", "补水", "防晒", "出行"],
    },
    {
        "title": "每日饮水打卡成为健康话题",
        "description": "社交平台流行每日饮水打卡，带动水杯、保温杯讨论。",
        "category": "健康",
        "keywords": ["喝水", "健康", "补水", "打卡", "水杯"],
    },
    {
        "title": "机械键盘客制化热度上升",
        "description": "电竞与办公人群讨论客制化键盘、轴体与键帽。",
        "category": "数码",
        "keywords": ["键盘", "电竞", "客制化", "轴体"],
    },
    {
        "title": "防晒科普与测评内容走红",
        "description": "夏季紫外线增强，防晒知识科普与产品测评内容受关注。",
        "category": "美妆",
        "keywords": ["防晒", "测评", "紫外线", "夏季"],
    },
    {
        "title": "热门手游新版本上线",
        "description": "多款头部手游更新版本，玩家讨论新角色、新玩法与赛季活动。",
        "category": "游戏",
        "keywords": ["手游", "版本更新", "新角色", "赛季", "玩家"],
    },
    {
        "title": "电竞联赛总决赛热度攀升",
        "description": "职业电竞联赛进入决赛阶段，观赛与应援内容爆发式增长。",
        "category": "游戏",
        "keywords": ["电竞", "决赛", "战队", "观赛", "应援"],
    },
    {
        "title": "主机游戏促销与独占新作受关注",
        "description": "主机平台促销与独占新作带动主机及周边配件关注度。",
        "category": "游戏",
        "keywords": ["主机", "游戏", "周边", "配件", "促销"],
    },
    {
        "title": "低卡零食成为健康新宠",
        "description": "健身与控糖人群追捧低卡、高蛋白零食，带动代餐与轻食讨论。",
        "category": "食品",
        "keywords": ["低卡", "零食", "高蛋白", "代餐", "控糖"],
    },
    {
        "title": "预制菜与快手菜走红",
        "description": "都市上班族关注省时省力的预制菜与快手菜，方便食品讨论升温。",
        "category": "食品",
        "keywords": ["预制菜", "快手菜", "方便食品", "上班族"],
    },
    {
        "title": "网红奶茶与咖啡新品争相上市",
        "description": "茶饮咖啡品牌密集上新，联名款与季节限定款引发打卡分享。",
        "category": "食品",
        "keywords": ["奶茶", "咖啡", "联名", "新品", "打卡"],
    },
    {
        "title": "新能源汽车价格与配置调整",
        "description": "多家新能源车企调整价格与配置，购车决策内容需求上升。",
        "category": "汽车",
        "keywords": ["新能源", "汽车", "降价", "购车", "续航"],
    },
    {
        "title": "宠物智能用品受追捧",
        "description": "养宠家庭关注自动喂食器、智能猫砂盆等宠物智能设备。",
        "category": "宠物",
        "keywords": ["宠物", "智能", "喂食器", "猫砂盆", "养宠"],
    },
    {
        "title": "居家收纳与好物改造流行",
        "description": "家居收纳、旧物改造类短视频走红，带动居家用品关注。",
        "category": "家居",
        "keywords": ["收纳", "居家", "改造", "好物", "整理"],
    },
    {
        "title": "城市夜跑与健身打卡兴起",
        "description": "都市人群兴起夜跑、健身房打卡，运动装备与营养补充讨论增加。",
        "category": "运动健身",
        "keywords": ["夜跑", "健身", "打卡", "运动装备", "增肌"],
    },
    {
        "title": "亲子研学与暑期游升温",
        "description": "暑期临近，亲子研学、夏令营与家庭出游规划内容热度上升。",
        "category": "母婴亲子",
        "keywords": ["亲子", "研学", "夏令营", "暑期", "家庭游"],
    },
]


def _heat(title: str, now: datetime) -> float:
    """用标题哈希 + 时间相位模拟缓慢变化的热度值（仅演示兜底用）。"""
    seed = sum(ord(c) for c in title)
    phase = now.timestamp() / 3600.0
    return 50.0 + 40.0 * math.sin(phase + seed % 10)


def _trend(prev: float, cur: float) -> str:
    if cur > prev * 1.05:
        return "rising"
    if cur < prev * 0.95:
        return "declining"
    return "peak" if cur > 70 else "stable"


def _match_category(text: str, mapping: dict) -> str:
    """按词表顺序匹配板块，命中第一个即返回。"""
    for category, words in mapping.items():
        for word in words:
            if word and word in text:
                return category
    return ""


def _map_source_hint(hint: str, mapping: dict) -> str:
    """来源自带分类（如 B 站分区 tname）→ 板块；先精确匹配，再包含匹配。"""
    hint = clean_text(hint)
    if not hint:
        return ""
    if hint in mapping:
        return mapping[hint]
    for key, category in mapping.items():
        if key and (key in hint or hint in key):
            return category
    return ""


def _extract_keywords(text: str, mapping: dict, limit: int = 6) -> list[str]:
    """从标题/摘要里抽取命中的词表关键词，作为热点关键词。"""
    hits: list[str] = []
    for words in mapping.values():
        for word in words:
            if word and word in text and word not in hits:
                hits.append(word)
    return hits[:limit]


def _score_items(items: list[dict]) -> None:
    """按来源分组把原始热度归一化到 0~100；没有热度值的源按名次折算。"""
    groups: dict[str, list[dict]] = {}
    for item in items:
        groups.setdefault(item.get("source") or "", []).append(item)

    for group in groups.values():
        group.sort(key=lambda x: x.get("rank") or 10**9)
        raws = [
            x.get("raw_hot") for x in group if isinstance(x.get("raw_hot"), (int, float))
        ]
        low = min(raws) if raws else None
        high = max(raws) if raws else None
        total = len(group) or 1
        for index, item in enumerate(group):
            raw = item.get("raw_hot")
            if isinstance(raw, (int, float)) and low is not None and high is not None and high > low:
                item["hot_score"] = round(30.0 + 70.0 * (raw - low) / (high - low), 2)
            else:
                item["hot_score"] = round(max(20.0, 100.0 - index * (75.0 / total)), 2)

    items.sort(key=lambda x: x.get("hot_score", 0.0), reverse=True)


def _upsert(db: Session, records: list[dict], now: datetime) -> tuple[int, int]:
    """按「来源 + 标题」去重写入；返回 (新增条数, 更新条数)。"""
    new_count = 0
    updated_count = 0
    for record in records:
        title = clean_text(record.get("title"))
        if not title:
            continue
        source = clean_text(record.get("source")) or "unknown"
        score = float(record.get("hot_score") or 50.0)
        existing = (
            db.query(Hotspot)
            .filter(Hotspot.title == title, Hotspot.source == source)
            .first()
        )
        if existing:
            history = list(existing.heat_history or [])
            prev = history[-1]["score"] if history else score
            history.append({"ts": now.isoformat(), "score": score})
            existing.heat_history = history[-24:]  # 保留最近 24 个快照
            existing.hot_score = score
            existing.trend = _trend(prev, score)
            existing.last_updated = now
            if record.get("description"):
                existing.description = record["description"]
            if record.get("source_url"):
                existing.source_url = record["source_url"]
            if record.get("category"):
                existing.category = record["category"]
            if record.get("keywords"):
                existing.keywords = record["keywords"]
            updated_count += 1
        else:
            db.add(
                Hotspot(
                    title=title,
                    description=clean_text(record.get("description")),
                    source=source,
                    source_url=clean_text(record.get("source_url")),
                    category=clean_text(record.get("category")),
                    keywords=list(record.get("keywords") or []),
                    hot_score=score,
                    trend="rising",
                    heat_history=[{"ts": now.isoformat(), "score": score}],
                    is_sensitive=False,
                    first_seen=now,
                    last_updated=now,
                )
            )
            new_count += 1
    return new_count, updated_count


def _collect_real(now: datetime) -> list[dict]:
    """采集真实热点并补齐板块 / 关键词 / 热度；返回空列表表示全部源不可用。"""
    raw = collect_all()
    if not raw:
        return []
    mapping = category_keywords()
    hint_map = source_category_map()
    for item in raw:
        text = f"{item.get('title', '')} {item.get('description', '')}"
        category = _match_category(text, mapping) or _map_source_hint(
            item.get("hint_category"), hint_map
        )
        # 热点榜里大量是泛资讯，无法归入具体板块时统一归到「综合」，保证看板不出现空标签
        item["category"] = category or "综合"
        item["keywords"] = _extract_keywords(text, mapping)
    _score_items(raw)
    return raw


def _demo_records(now: datetime) -> list[dict]:
    records: list[dict] = []
    for index, item in enumerate(DEMO_HOTSPOTS, start=1):
        records.append(
            {
                "title": item["title"],
                "description": item["description"],
                "source": "demo",
                "source_url": "",
                "category": item["category"],
                "keywords": item["keywords"],
                "hot_score": round(_heat(item["title"], now), 2),
                "rank": index,
            }
        )
    _score_items(records)
    return records


def collect_hotspots(db: Session) -> dict:
    """采集热点并更新热度快照。返回 {collected,new,updated,total,demo[,running]} 明细。

    - 用互斥锁串行化：定时任务（后台线程）与手动「立即采集」（请求线程）不会并发执行；
      若已有采集在进行，本次直接返回 running=True，前端提示「稍后再试」而不是报错。
    - 任何异常都会 rollback 后向上抛，避免脏事务影响后续请求。
    """
    if not _COLLECT_LOCK.acquire(blocking=False):
        total = db.query(Hotspot).count()
        logger.info("已有采集任务在执行，跳过本次触发")
        return {
            "collected": 0,
            "new": 0,
            "updated": 0,
            "total": total,
            "demo": False,
            "running": True,
            "message": "上一次采集还在进行中，请等几秒后再点一次",
        }
    try:
        return _collect_locked(db)
    except Exception:
        db.rollback()
        logger.exception("热点采集失败")
        raise
    finally:
        _COLLECT_LOCK.release()


def _collect_locked(db: Session) -> dict:
    now = datetime.now(timezone.utc)
    records = _collect_real(now)
    if records:
        new_count, updated_count = _upsert(db, records, now)
        db.commit()
        logger.info("真实热点入库：新增 %d 条，更新 %d 条", new_count, updated_count)
        return {
            "collected": new_count + updated_count,
            "new": new_count,
            "updated": updated_count,
            "total": db.query(Hotspot).count(),
            "demo": False,
        }

    # 真实源全部不可用：只有库里完全没有热点时才写演示数据，避免演示数据混进真实数据
    if db.query(Hotspot).count() > 0:
        logger.warning("真实热点源均不可用，保留库中已有热点")
        return {
            "collected": 0,
            "new": 0,
            "updated": 0,
            "total": db.query(Hotspot).count(),
            "demo": False,
        }

    new_count, updated_count = _upsert(db, _demo_records(now), now)
    db.commit()
    logger.warning("真实热点源均不可用，已写入 %d 条演示热点", new_count)
    return {
        "collected": new_count + updated_count,
        "new": new_count,
        "updated": updated_count,
        "total": db.query(Hotspot).count(),
        "demo": True,
    }
