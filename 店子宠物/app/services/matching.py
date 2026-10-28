"""模块二：热点与商品的两阶段匹配。

第一阶段（程序）：关键词 + 语义相似度 + 时效 + 敏感过滤，筛出候选。
第二阶段（大模型）：业务判断，输出关联逻辑、创意、风险与 P/C 分项。
最后按 S = 0.30R + 0.25T + 0.20A + 0.15P + 0.10C 汇总，生成机会卡。
"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..config import load_yaml
from ..embedding import semantic_similarity, tokenize
from ..llm import llm
from ..models import Hotspot, Match, Product
from ..schemas import MatchBatch, MatchJudge, MatchOpportunity

# 评分权重（启动假设值，需按真实运营数据迭代）
W = {"r": 0.30, "t": 0.25, "a": 0.20, "p": 0.15, "c": 0.10}

logger = logging.getLogger(__name__)

JUDGE_SYSTEM = """你是热点营销判断专家。给定商品与热点，判断是否值得跟进，并输出结构化结果。
必须回答：
1. 热点与商品之间存在什么真实联系？（直接 / 间接 / 牵强）
2. 目标消费者是否可能感兴趣？
3. 是否存在事实错误、侵权或品牌声誉风险？
4. 若值得跟进，适合什么内容形式？
p_score 表示营销潜力（0-100），c_score 表示证据可信度（0-100）。
牵强附会、敏感事件、无法证实的功效，应设 should_skip=true 并写入 risk。
严格按 JSON Schema 输出。"""


def _tag_terms(p: Product) -> list[str]:
    """把商品的多维标签拍平成词表，供匹配时做「标签命中」。"""
    tags = getattr(p, "tags", None) or {}
    terms: list[str] = []
    for value in tags.values():
        if isinstance(value, list):
            terms.extend(str(x) for x in value if x)
        elif value:
            terms.append(str(value))
    return terms


def _product_text(p: Product) -> str:
    parts = [p.name, p.brand, p.category] + list(p.selling_points or [])
    parts += [str(v) for v in (p.specs or {}).values()]
    if p.target_audience:
        for k, v in p.target_audience.items():
            parts.append(str(v))
    parts += _tag_terms(p)
    return " ".join(parts)


def _hotspot_text(h: Hotspot) -> str:
    return " ".join([h.title, h.description] + list(h.keywords or []))


def relevance_score(p: Product, h: Hotspot) -> float:
    """R：语义相似度 + 关键词重叠 + 标签命中，归一化 0~100。

    有 AI 标签时，「标签词直接出现在热点文本里」是比整段 TF-IDF 更可靠的信号，
    因此单独给一份权重；没有标签时退回原来的 70/30 权重，不影响老数据。
    """
    product_text = _product_text(p)
    hotspot_text = _hotspot_text(h)
    sim = semantic_similarity(product_text, hotspot_text)  # 0~1
    p_words = set(tokenize(product_text))
    h_words = set(tokenize(hotspot_text))
    overlap = len(p_words & h_words) / (len(h_words) or 1)

    tags = _tag_terms(p)
    if tags:
        tag_hit = sum(1 for t in tags if t in hotspot_text) / len(tags)
        return min(100.0, round(sim * 50 + overlap * 20 + tag_hit * 30, 1))
    return min(100.0, round(sim * 70 + overlap * 30, 1))


def _age_hours(dt: datetime | None) -> float:
    """距今天数（小时）。

    SQLite 存下来的 datetime 是 naive 的（没有时区），直接用 aware 的 now 相减会
    抛「can't subtract offset-naive and offset-aware datetimes」，这里统一补成 UTC。
    """
    if dt is None:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def timeliness_score(h: Hotspot) -> float:
    """T：趋势 + 新鲜度，0~100。"""
    base = {"rising": 90, "peak": 80, "stable": 55, "declining": 30}.get(h.trend, 50)
    if _age_hours(h.last_updated) < 6:
        base += 10
    return min(100.0, base)


def audience_score(p: Product, h: Hotspot) -> float:
    """A：目标用户（场景/需求/顾虑）与热点关键词的重合度，0~100。"""
    if not p.target_audience:
        return 50.0
    aud = " ".join(str(v) for v in p.target_audience.values())
    a_words = set(tokenize(aud))
    h_words = set(tokenize(_hotspot_text(h)))
    overlap = len(a_words & h_words) / (len(a_words) or 1)
    return min(100.0, round(overlap * 100, 1))


def _is_fresh(h: Hotspot, max_hours: int = 72) -> bool:
    return _age_hours(h.last_updated) < max_hours


def _is_sensitive(h: Hotspot, p: Product) -> bool:
    risk_cfg = load_yaml("risk_keywords.yaml")
    text = _hotspot_text(h)
    for w in risk_cfg.get("sensitive_topics", []) + risk_cfg.get("negative_terms", []):
        if w in text:
            return True
    return False


BATCH_JUDGE_SYSTEM = """你是资深热点营销操盘手。给定一个商品和一批候选热点标题，挑出最值得跟进的机会（最多 2 个），并给出结构化判断。
挑选标准：
1. 热点与商品之间必须有真实联系：要么是直接使用场景，要么是目标人群/情绪上的间接关联；纯字面巧合、牵强附会的不要选。
2. 目标消费者（看商品的目标用户标签）可能感兴趣、会点开看。
3. 敏感事件、负面新闻、无法证实的功效、容易引战或损害品牌形象的热点一律不选。
4. 如果这批热点里确实没有合适的，opportunities 返回空数组——宁缺毋滥。
每个机会要给出：
- hotspot_index：候选热点编号（从 0 开始，必须是列表里真实存在的编号）
- relevance_reason：一句话说清「热点 × 商品」的真实联系
- marketing_idea：具体可执行的内容创意（做什么内容、从什么角度切入）
- content_form：建议的内容形式（如短视频开箱 / 图文清单 / 场景口播）
- p_score：营销潜力 0-100（这个热点能带来多大传播/转化机会）
- c_score：证据可信度 0-100（热点信息是否可靠、是否容易被证伪）
- r_score：关联强度 0-100（热点与商品的联系有多强：直接使用场景 80 分以上，目标人群/情绪上的间接关联 55-79，需要绕很多弯的 40 以下）
- risk：风险列表（侵权、敏感、夸大宣传等）
- checklist：发布前检查项
- should_skip：只有拿不准时才设 true
严格按 JSON Schema 输出。"""


def _ai_select(p: Product, pool: list[Hotspot]) -> list[MatchOpportunity]:
    """让大模型直接从候选热点里挑机会（语义选择，比 TF-IDF 更懂「防晒霜 × 妆容」这类关联）。"""
    lines = [f"{i}. {h.title}" for i, h in enumerate(pool)]
    user = f"商品：\n{_product_text(p)}\n\n候选热点（编号: 标题）：\n" + "\n".join(lines)
    return llm.complete_json(BATCH_JUDGE_SYSTEM, user, MatchBatch).opportunities


def _judge(p: Product, h: Hotspot, r: float) -> MatchJudge:
    """第二阶段：大模型业务判断（Mock 时用启发式）。"""
    if llm.is_mock:
        return _mock_judge(p, h, r)
    user = f"商品：\n{_product_text(p)}\n\n热点：\n{h.title}\n{h.description}\n关键词：{h.keywords}\n程序相关性分：{r}"
    return llm.complete_json(JUDGE_SYSTEM, user, MatchJudge)


def _mock_judge(p: Product, h: Hotspot, r: float) -> MatchJudge:
    name = p.name or "该商品"
    if r < 50:
        return MatchJudge(
            relevance_reason=f"热点与「{name}」关联较弱，不建议硬蹭。",
            marketing_idea="",
            content_form="",
            p_score=20,
            c_score=50,
            risk=["关联牵强，可能损害品牌专业形象"],
            checklist=[],
            should_skip=True,
        )
    reason = f"热点与「{name}」的 {p.category or '品类'} 存在使用场景关联，可从真实使用场景切入。"
    idea = f"围绕热点制作与「{name}」相关的场景化内容，突出 {('、'.join(p.selling_points or ['卖点'])[:20])}。"
    return MatchJudge(
        relevance_reason=reason,
        marketing_idea=idea,
        content_form="短视频开箱 + 图文清单",
        p_score=min(100, int(r)),
        c_score=60,
        risk=["需核对热点来源与发布时间，产品参数以实物为准"],
        checklist=["核对热点来源与发布时间", "核对产品参数与图片版权", "检查平台规范"],
        should_skip=False,
    )


def run_matching(
    db: Session,
    product_ids=None,
    hotspot_ids=None,
    hotspot_limit: int = 40,
    judge_limit: int = 12,
) -> list[Match]:
    """两阶段匹配（真实大模型时是「AI 挑选 + AI 判断」）。

    第一阶段（程序）：由热榜采集结果里取新鲜、非敏感的候选热点，并给每对算好 R/T/A 分项。
    第二阶段（大模型）：逐个商品把候选热点标题列表交给模型，让它自己挑出最多 2 个真实可结合的机会
    （TF-IDF 只能算字面重合，挑不出「防晒霜 × 妆容」这种语义关联），再由它给出创意、风险与 P/C 分。
    最后按 S = 0.30R + 0.25T + 0.20A + 0.15P + 0.10C 汇总成机会卡；已存在的商品×热点组合会原位刷新，重复点击不会堆重复卡。
    """
    products = db.query(Product).all()
    if product_ids:
        products = [p for p in products if p.id in product_ids]

    query = db.query(Hotspot).filter(Hotspot.is_sensitive.is_(False))
    if hotspot_ids:
        hotspots = [h for h in query.all() if h.id in hotspot_ids]
    else:
        hotspots = query.order_by(Hotspot.hot_score.desc()).limit(hotspot_limit).all()

    valid = [h for h in hotspots if _is_fresh(h) and not _is_sensitive(h, None)]
    pool = valid[:hotspot_limit]
    existing = {(m.product_id, m.hotspot_id): m for m in db.query(Match).all()}

    def _make(p: Product, h: Hotspot, judge: MatchJudge) -> Match:
        # 真实大模型会给 r_score（语义关联强度）；TF-IDF 只按字面重合算，真实热榜下普遍只有 0~10，
        # 所以在有 AI 判定时用它，另存一份 r_tfidf 备查；Mock 模式仍然用 TF-IDF。
        ai_r = getattr(judge, "r_score", None)
        r = float(ai_r) if ai_r is not None else relevance_score(p, h)
        subscores = {
            "r": r,
            "r_tfidf": relevance_score(p, h),
            "t": timeliness_score(h),
            "a": audience_score(p, h),
            "p": judge.p_score,
            "c": judge.c_score,
        }
        return Match(
            hotspot_id=h.id,
            product_id=p.id,
            score=round(sum(W[k] * subscores[k] for k in W), 1),
            subscores=subscores,
            relevance_reason=judge.relevance_reason,
            marketing_idea=judge.marketing_idea,
            content_form=judge.content_form,
            risk=judge.risk,
            checklist=judge.checklist,
            status="pending",
        )

    def _picks(p: Product) -> list[tuple[Hotspot, MatchJudge]]:
        """算单个商品的候选机会（第二阶段）。真实大模型时商品间无依赖，可并发。"""
        if llm.is_mock or not pool:
            scored = sorted(((relevance_score(p, h), h) for h in pool), key=lambda x: x[0], reverse=True)
            return [(h, _mock_judge(p, h, r)) for r, h in scored[:2]]
        out: list[tuple[Hotspot, MatchJudge]] = []
        try:
            for opp in _ai_select(p, pool)[:2]:
                if opp.should_skip or not (0 <= opp.hotspot_index < len(pool)):
                    continue
                out.append((pool[opp.hotspot_index], opp))
        except Exception as exc:  # 单个商品 LLM 失败只告警，不拖垮整次匹配
            logger.warning("商品 %s 的 AI 挑选失败：%s", getattr(p, "id", "?"), exc)
        return out

    # 第二阶段：真实大模型时逐商品并行交给模型（商品间无依赖），显著缩短总耗时
    if llm.is_mock or not pool or len(products) <= 1:
        picks_map = [(p, _picks(p)) for p in products]
    else:
        with ThreadPoolExecutor(max_workers=min(len(products), 6)) as ex:
            futures = [ex.submit(_picks, p) for p in products]
            picks_map = [(p, f.result()) for p, f in zip(products, futures)]

    results: list[Match] = []
    for p, picks in picks_map:
        if len(results) >= judge_limit:
            break
        for h, judge in picks:
            if len(results) >= judge_limit:
                break
            fresh = _make(p, h, judge)
            row = existing.get((p.id, h.id))
            if row is not None:
                # 重复点击：原位刷新已有机会卡，不产生重复行，也保证每次点击都有结果可看
                row.score = fresh.score
                row.subscores = fresh.subscores
                row.relevance_reason = fresh.relevance_reason
                row.marketing_idea = fresh.marketing_idea
                row.content_form = fresh.content_form
                row.risk = fresh.risk
                row.checklist = fresh.checklist
                row.status = "pending"
                results.append(row)
            else:
                db.add(fresh)
                existing[(p.id, h.id)] = fresh
                results.append(fresh)
    db.commit()
    # commit 后对象会过期（expire_on_commit），不 refresh 的话接口序列化出来会是空对象
    for m in results:
        db.refresh(m)
    return results
