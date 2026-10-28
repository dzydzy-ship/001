"""模块一步骤 2：将商家零散资料结构化提取为统一商品字段。

原则：只提取资料中明确出现的信息，缺失字段标记为「待确认」，不让模型凭空补充。

「更聪明的提取」分两层：
1. 抽取层：规则（Mock/无 Key 时）+ 大模型（有 Key 时）双通道，产出 ProductExtract；
2. 分析层（_analyze，纯规则、秒回）：给每个字段标「信息类别 / 来源 / 置信度」，
   并把资料里的价格拆成 成本/标价/批发/促销 四类，最后列出需要商家确认的点。
"""
import re
from typing import Dict, List, Optional

from ..llm import llm
from ..schemas import ExtractPreview, ProductExtract

EXTRACT_SYSTEM = """你是商品信息结构化专家，负责把商家提供的商品资料整理成统一字段。
规则：
1. 只提取资料中明确出现的信息；未出现的字段不要编造，把字段名加入 missing_fields。
2. name 是商品核心名称（去掉"商品名称："等前缀）；brand 是品牌名，资料未提则留空。
3. specs 是规格参数（容量、材质、尺寸、功率、型号、续航等键值对）；selling_points 是核心卖点（功能、材质、设计、差异化优势，每条简短独立）。
4. target_audience 包含年龄段、场景、需求、顾虑四类信息；资料未提就留空，不要硬猜。
5. evidence 是检测报告、认证材料等；restrictions 是禁用表述或合规限制。
6. price 是零售/标价；cost_price 是出厂/进货成本。只填数字（单位元），资料未明确给出则留空，不要从卖点臆测价格。
7. 同一信息不要同时放进 specs 和 selling_points，参数归 specs、卖点归 selling_points。
8. 输出前自查：每个字段都能在原文中找到依据，否则写入 missing_fields。
9. tags 是给商品自动打的多维标签（用于和热点做匹配），必须基于资料推断：
   - 品类：1~2 个，如 "保温杯"、"遥控飞机"；
   - 功能：2~4 个，如 "保温"、"便携"、"到手飞"；
   - 场景：1~3 个，如 "通勤"、"户外"、"送礼"；
   - 人群：1~3 个，如 "学生"、"上班族"、"航空迷"；
   - 痛点：1~3 个，如 "怕漏水"、"易摔"、"难上手"；
   - 价格带：按 price 判断，如 "百元内" / "中端" / "高端"；
   - 可蹭热点类型：1~3 个，如 "季节"、"节日"、"健康生活"、"户外露营"、"开学季"、"低空经济"。
   tags 的值都是字符串数组；没有依据的维度直接省略，不要编造。
10. 营销话术（如"最好用""销量第一""行业领先"）不要当作事实证据，也不要写进 evidence。
"""

# ---------------------------------------------------------------------------
# 资料里出现的价格关键词 → 价格角色
# ---------------------------------------------------------------------------
PRICE_PATTERNS: Dict[str, str] = {
    "成本": r"(?:出厂价|成本价|进货价|供货价|采购价|进价|成本)[^\d]{0,8}(\d+(?:\.\d+)?)",
    "标价": r"(?:零售控价|零售价|标价|售价|定价|原价|销售价)[^\d]{0,8}(\d+(?:\.\d+)?)",
    "批发": r"(?:批发控价|批发价|渠道价|分销价|批发)[^\d]{0,8}(\d+(?:\.\d+)?)",
    "促销": r"(?:促销价|活动价|券后价|优惠价|到手价)[^\d]{0,8}(\d+(?:\.\d+)?)",
}

# 字段 → 信息类别
FIELD_CLASSIFY: Dict[str, str] = {
    "name": "事实类", "brand": "事实类", "category": "事实类",
    "price": "价格类", "cost_price": "价格类",
    "specs": "规格类", "selling_points": "营销类",
    "target_audience": "营销类", "evidence": "事实类",
    "restrictions": "合规类", "tags": "营销类",
}

# 显式关键词（用于判定「原文显式」）
EXPLICIT_RULES: Dict[str, str] = {
    "name": r"(?:产品名称|商品名称|商品名|产品名|名称|品名)[:：]",
    "brand": r"(?:品牌)[:：]",
    "category": r"(?:品类|类目|分类|类别)[:：]",
    "price": r"(?:零售控价|零售价|标价|售价|定价|原价)[:：]?\s*[¥￥]?\d",
    "cost_price": r"(?:出厂价|成本价|进货价|供货价|厚度成本|成本)[:：]?\s*[¥￥]?\d",
    "selling_points": r"(?:卖点|产品特点|特点|亮点|核心卖点|优势|主打)[:：]",
    "specs": r"(?:产品规格|规格|参数|商品规格)[:：]",
    "evidence": r"(?:证书|认证|检测报告|资质)",
    "target_audience": r"(?:适用场景|场景|适用人群|人群|目标用户)[:：]",
    "restrictions": r"(?:禁用|不得|限制|禁忌)[:：]",
}

_SPEC_KEYS = (
    "容量|材质|尺寸|重量|功率|颜色|型号|保温时长|续航|配列|轴体|连接|规格|SPF|克数|体积|口味|包装|"
    "飞行时间|充电时间|充电方式|遥控器|动力电池|遥控器电池|飞机尺寸|毛重|净重|箱规|装箱数量|彩盒尺寸"
)


def extract_product(raw_material: str) -> ExtractPreview:
    """从原始资料提取结构化商品信息（返回带分类/置信度/待确认的预览）。"""
    raw = (raw_material or "").strip()
    if not raw:
        return ExtractPreview(missing_fields=["全部字段：未提供商品资料"], review=["请先粘贴或输入商品资料"])

    source = "规则推断"
    if llm.is_mock:
        base = _mock_extract(raw)
    else:
        base = llm.complete_json(EXTRACT_SYSTEM, f"商品资料：\n{raw}", ProductExtract)
        source = "模型推断"
    return _analyze(raw, base, source)


# ---------------------------------------------------------------------------
# 分析层：分类 / 来源 / 置信度 / 价格四类 / 待确认
# ---------------------------------------------------------------------------
def _explicit_keys(raw: str) -> set:
    found = set()
    for field, pattern in EXPLICIT_RULES.items():
        if re.search(pattern, raw):
            found.add(field)
    return found


def _price_roles(raw: str) -> Dict[str, Optional[float]]:
    roles: Dict[str, Optional[float]] = {k: None for k in PRICE_PATTERNS}
    for role, pattern in PRICE_PATTERNS.items():
        m = re.search(pattern, raw)
        if m:
            roles[role] = float(m.group(1))
    return roles


def _confidence(field: str, value, explicit: set, base_source: str) -> float:
    if value in (None, "", [], {}):
        return 0.0
    if field in explicit:
        return 0.95
    if base_source == "模型推断":
        return 0.8
    # 规则推断：卖点/人群这类主观字段天然分数低一点
    if field in ("selling_points", "target_audience", "tags"):
        return 0.6
    return 0.7


def _analyze(raw: str, base: ProductExtract, base_source: str) -> ExtractPreview:
    explicit = _explicit_keys(raw)
    roles = _price_roles(raw)

    # 规则补位：价格/成本为空时，从资料里的价格角色补（不覆盖已有值）
    if base.cost_price is None and roles["成本"] is not None:
        base.cost_price = roles["成本"]
        explicit.add("cost_price")
    if base.price is None:
        if roles["标价"] is not None:
            base.price = roles["标价"]
            explicit.add("price")
        elif roles["批发"] is not None:
            base.price = roles["批发"]
            explicit.add("price")

    values = {
        "name": base.name, "brand": base.brand, "category": base.category,
        "price": base.price, "cost_price": base.cost_price,
        "specs": base.specs, "selling_points": base.selling_points,
        "target_audience": base.target_audience, "evidence": base.evidence,
        "restrictions": base.restrictions, "tags": base.tags,
    }
    confidence: Dict[str, float] = {}
    field_sources: Dict[str, str] = {}
    classified: Dict[str, str] = {}
    for field, value in values.items():
        classified[field] = FIELD_CLASSIFY.get(field, "其他")
        if value in (None, "", [], {}):
            field_sources[field] = "缺失"
            confidence[field] = 0.0
        else:
            field_sources[field] = "原文显式" if field in explicit else base_source
            confidence[field] = _confidence(field, value, explicit, base_source)

    review: List[str] = []
    if base.price is None and base.cost_price is None:
        review.append("资料里没有明确价格 / 成本：利润防护无法测算，请补充「出厂价」或「售价」。")
    if base.price is not None and base.cost_price is None:
        review.append(f"只有售价（{base.price:g}）没有成本：能算卖多少钱、算不了赚多少。")
    if roles["成本"] is not None and roles["标价"] is not None and roles["成本"] > roles["标价"]:
        review.append("成本价高于售价，请核对是否写反了。")
    if roles["批发"] is not None:
        review.append(f"识别到批发指导价 {roles['批发']:g}：它通常不是电商零售价，已单独留档，未当售价使用。")
    if roles["促销"] is not None:
        review.append(f"识别到促销价 {roles['促销']:g}：建议先按标价做利润测算，促销价单独用利润防护验证。")
    if not base.evidence:
        review.append("资料里没有可核验的证据（证书/检测报告）：生成内容时会避免写「认证」「专利」类背书。")
    if base.missing_fields:
        review.append("待确认字段：" + "、".join(base.missing_fields[:8]))
    marketing_hits = re.findall(r"(最好|第一|顶级|绝对|100%|行业领先|全网最低)", raw)
    if marketing_hits:
        review.append("资料含营销话术（" + "、".join(sorted(set(marketing_hits))[:5]) + "），未当作事实证据。")

    return ExtractPreview(
        **base.model_dump(),
        confidence={k: round(v, 2) for k, v in confidence.items()},
        field_sources=field_sources,
        price_roles=roles,
        classified=classified,
        review=review,
    )


# ---------------------------------------------------------------------------
# 规则化提取（无 API Key 时的兜底，也用于离线演示）
# ---------------------------------------------------------------------------
def _mock_extract(raw: str) -> ProductExtract:
    """规则化提取：演示用，保证无 API Key 时也能产出结构化结果。"""
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    text = raw.replace("\n", "，")

    # 名称：去掉常见前缀
    name = ""
    m = re.search(r"(?:商品名称|商品名|产品名称|产品名|名称|品名)[:：]\s*([^\s,，。;；]+)", text)
    if m:
        name = m.group(1)
    if not name and lines:
        name = re.sub(r"^[（(【\[].*?[）)】\]]", "", lines[0])[:50]
    name = (name or "未命名商品").strip("，。；;:： ")

    # 品牌
    brand = ""
    m = re.search(r"(?:品牌)[:：]\s*([^\s,，。;；]+)", text)
    if m:
        brand = m.group(1)

    # 价格 / 成本（按角色关键词优先）
    roles = _price_roles(raw)
    price = roles["标价"] if roles["标价"] is not None else roles["批发"]
    cost = roles["成本"]
    if price is None:
        m = re.search(r"(?:价格|售价|定价|原价|¥|￥)[:：]?\s*(\d+(?:\.\d+)?)\s*元?", text)
        if m:
            price = float(m.group(1))
    if cost is None:
        m = re.search(r"(?:成本|成本价|进货价)[:：]?\s*(\d+(?:\.\d+)?)\s*元?", text)
        if m:
            cost = float(m.group(1))

    # 规格：键值对（更通用：任意「短键：值」都收，但排除名称/品牌/价格类键）
    specs: Dict[str, str] = {}
    skip_keys = {"商品名称", "商品名", "产品名称", "产品名", "名称", "品名", "品牌", "品类", "类目",
                 "类别", "分类", "价格", "售价", "定价", "原价", "成本", "成本价", "进货价",
                 "出厂价", "零售价", "零售控价", "批发价", "批发控价", "促销价", "活动价",
                 "到手价", "券后价", "供货价", "采购价", "进价",
                 "卖点", "优势", "特点", "功能", "亮点", "主打", "适用场景", "场景", "适用人群", "人群"}
    for m in re.finditer(r"([\u4e00-\u9fa5A-Za-z0-9（）()]{2,12})[:：]\s*([^\s,，。;；|]{1,40})", text):
        key, value = m.group(1).strip(), m.group(2).strip()
        if key in skip_keys or key in specs:
            continue
        specs[key] = value
    # 兜底：已知规格键表
    if not specs:
        for m in re.finditer(rf"({_SPEC_KEYS})[:：]?\s*([^\s,，。;；]+)", text):
            specs[m.group(1)] = m.group(2)

    # 卖点
    selling_points: List[str] = []
    for ln in lines:
        if re.match(r"^(卖点|优势|特点|功能|亮点|主打)[:：]", ln):
            pt = re.split(r"[:：]", ln, maxsplit=1)[-1].strip()
            selling_points.extend([x.strip() for x in re.split(r"[、,，;；]", pt) if x.strip()])
    if not selling_points:
        selling_points = [f"{k}：{v}" for k, v in list(specs.items())[:3]]

    # 品类
    category = ""
    m = re.search(r"(?:品类|类目|类别|分类)[:：]?\s*([^\s,，。;；]+)", text)
    if m:
        category = m.group(1)

    # 目标用户：场景/人群
    target_audience: Dict = {}
    m = re.search(r"(?:适用场景|场景|适用人群|人群|目标用户)[:：]?\s*([^。;；\n]+)", text)
    if m:
        target_audience["场景"] = [x.strip() for x in re.split(r"[、,，]", m.group(1)) if x.strip()]

    # 证据 / 限制
    evidence: List[str] = []
    m = re.search(r"(?:证书|认证|检测报告|资质)[:：]?\s*([^。;；\n]+)", text)
    if m:
        evidence = [x.strip() for x in re.split(r"[、,，;；]", m.group(1)) if x.strip()]
    restrictions: List[str] = []
    m = re.search(r"(?:禁用|不得|限制|禁忌)[:：]?\s*([^。;；\n]+)", text)
    if m:
        restrictions = [x.strip() for x in re.split(r"[、,，;；]", m.group(1)) if x.strip()]

    # 标签：规则版按已有字段拼装多维标签
    tags: Dict = {}
    if category:
        tags["品类"] = [category]
    if specs:
        tags["功能"] = list(specs.keys())[:4]
    scenes = target_audience.get("场景") or []
    if scenes:
        tags["场景"] = list(scenes)[:3]
    if selling_points:
        tags["痛点"] = [sp[:14] for sp in selling_points[:3]]
    if price is not None:
        if price < 100:
            tags["价格带"] = ["百元内"]
        elif price < 500:
            tags["价格带"] = ["中端"]
        else:
            tags["价格带"] = ["高端"]
    hot_types = [t for t in ["季节", "节日", "健康生活", "户外露营", "开学季", "低空经济", "送礼"] if t in raw]
    if hot_types:
        tags["可蹭热点类型"] = hot_types[:3]

    missing = []
    if not specs:
        missing.append("规格")
    if not category:
        missing.append("品类")
    if not brand:
        missing.append("品牌")
    if price is None:
        missing.append("价格")
    if cost is None:
        missing.append("成本")
    if not target_audience:
        missing.append("目标用户")
    missing.append("证据")  # 规则化无法可靠推断

    return ProductExtract(
        name=name,
        brand=brand,
        category=category,
        specs=specs,
        price=price,
        cost_price=cost,
        selling_points=selling_points,
        target_audience=target_audience,
        evidence=evidence,
        restrictions=restrictions,
        tags=tags,
        missing_fields=missing,
    )
