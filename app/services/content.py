"""模块一：将结构化商品信息按平台策略生成宣传内容。

流程（v2）：
1. **生成前产品分析**：从结构化商品字段提炼「事实清单」——卖点、人群、场景、痛点、
   证据、禁忌、允许出现的数字、资料缺口。不新增任何事实。
2. **加载平台 Skill**：config/platform_skill_<platform>.md（小红书/B站/抖音/电商详情页）
   注入提示词，让模型按该平台的算法与写法产出内容。
3. **大模型生成**：只允许使用事实清单里的信息。
4. **事实校验（防幻觉）**：程序化检查生成内容里的数字、成本泄露、无依据宣称、
   商品名命中；发现严重问题（编造价格/无依据认证）自动重试一次。
5. **合规校验**：audit_content（禁用词、长度、标签数）。

数字与事实永远来自商品资料；模型只负责"按平台写法把事实讲好听"。
"""
import json
import re

from ..config import load_yaml
from ..llm import llm
from ..models import Product
from ..schemas import ContentGenerate
from .platform_skill import load_platform_skill

GEN_SYSTEM = """你是资深电商内容运营。根据商品结构化资料与平台策略生成宣传内容。
要求：
1. 只使用「产品分析/事实清单」中真实存在的信息，禁止编造功效、价格、参数、销量、证书、时间。
2. 遵守平台的禁用表述与标题/标签长度限制。
3. content_type 为 post 时填写 title/body/tags/cta；为 script 时额外填写 script 分镜（scene/text/note）。
4. script 分镜要足够详细：至少 6 个分镜，每个分镜的 text 是可直接口播的完整台词（具体生动、有画面感），note 写清景别、运镜、字幕、时长等拍摄提示。
5. 平台策略若标注 emoji=true，则 title 和 body 要自然地使用 emoji 增强可读性与亲和力。
6. 严格按 JSON Schema 输出。
7. 必须遵守下方「平台宣传 Skill」里的标题公式、开头钩子、结构与红线；Skill 与硬性限制冲突时以硬性限制为准。
8. 文案里出现的每个数字都必须来自「允许出现的数字」；资料缺口处（未填写的字段）不要硬编，改用弱化表述或回避。
9. 不得出现进货成本、利润率等内部数据；不得出现"亲测有效"等无法验证的背书。

【去 AI 味·五条硬规则（最高优先级，违反即视为不合格）】
A. 禁止"冒号词条堆叠"：不要写「特点：xx；优势：xx；适用人群：xx」这种列表式句子。
B. 只讲 1 个主卖点：其余卖点最多各带一句，不许平均用力、不许把参数念一遍。
C. 第一人称体验：用"我/我家/我们"，写一个具体的时间、地点、场景或小麻烦，让读者能代入。
D. 口语化：句子有长有短、能用逗号断开就说断；不使用"本品/该产品/优质材料/品质卓越/匠心"等广告腔。
E. 自然收尾：用提问或"想了解的点关注/评论区问"收尾，禁止"快来购买/不容错过/赶紧入手/三二一上链接"。
另：正文不要每段一样长；可以主动说一个真实的小缺点，这比全夸更可信。"""


EMOJIS = ["✨", "🔥", "💡", "👍", "💯", "🎯", "😍", "🌟", "✅", "🎉"]

# 数字 + 常见单位（用于事实校验）
_NUM_RE = re.compile(
    r"(?:[¥￥]\s*)?(\d+(?:\.\d+)?)\s*"
    r"(%|元|块|ml|mL|毫升|L|升|克|g|kg|千克|小时|h|分钟|天|个月|年|次|档|件|倍|万|人|cm|mm|米|英寸|寸|W|w|V|v|A|mA|Hz)?"
)

# 没有证据就不能出现的宣称词
_CLAIM_WORDS = [
    "专利", "认证", "获奖", "销量第一", "销量冠军", "第一名", "国家级",
    "权威机构", "检测报告", "质检报告", "疗效", "治疗", "根治", "包治",
    "无副作用", "零风险", "100%有效",
]

_MONEY_UNITS = {"元", "块", "¥", "￥"}


# ---------------------------------------------------------------------------
# 平台配置
# ---------------------------------------------------------------------------


def get_platform_config(platform: str) -> dict:
    cfg = load_yaml("platforms.yaml")["platforms"]
    if platform not in cfg:
        raise ValueError(f"未知平台: {platform}")
    return cfg[platform]


def list_platforms() -> list[dict]:
    return [{"key": k, **v} for k, v in load_yaml("platforms.yaml")["platforms"].items()]


# ---------------------------------------------------------------------------
# 生成前产品分析
# ---------------------------------------------------------------------------


def _as_list(v) -> list:
    """把 JSON 字段统一成字符串列表（dict 转 k：v）。"""
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    if isinstance(v, dict):
        return [f"{k}：{val}" for k, val in v.items() if str(val).strip()]
    s = str(v).strip()
    return [s] if s else []


def _norm_num(raw: str) -> str:
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return raw
    return f"{f:g}"


def _numbers_in(text) -> set:
    return {_norm_num(m.group(1)) for m in _NUM_RE.finditer(str(text or ""))}


def _dedup(items, limit=None) -> list:
    seen, out = set(), []
    for x in items:
        x = str(x).strip()
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out[:limit] if limit else out


def _allowed_numbers(p: Product) -> list:
    """允许在文案中出现的数字集合（价格、规格、卖点、促销、证据、名称里的数字）。"""
    nums = set()
    for v in (p.price, p.cost_price, p.name, p.category, p.promotion, p.brand_tone):
        nums |= _numbers_in(v)
    for field in (p.specs, p.selling_points, p.target_audience, p.evidence,
                  p.restrictions, p.tags):
        nums |= _numbers_in(json.dumps(field, ensure_ascii=False) if not isinstance(field, str) else field)
    return sorted(nums)


def build_product_brief(p: Product) -> dict:
    """生成前产品分析：只做提炼与归类，绝不新增事实。"""
    tags = p.tags or {}
    tag_list = _as_list(tags)
    core_points = _dedup(
        _as_list(p.selling_points) + _as_list(tags.get("功能")),
        limit=8,
    )
    audience = {
        "描述": _as_list(p.target_audience.get("年龄段")) + _as_list(tags.get("人群")),
        "需求": _as_list(p.target_audience.get("需求")),
        "顾虑": _as_list(p.target_audience.get("顾虑")),
    }
    scenes = _dedup(_as_list(p.target_audience.get("场景")) + _as_list(tags.get("场景")), limit=6)
    pain_points = _dedup(_as_list(tags.get("痛点")) + _as_list(p.target_audience.get("顾虑")), limit=6)
    evidence = _as_list(p.evidence)
    restrictions = _dedup(_as_list(p.restrictions), limit=10)
    hot_topic_types = _dedup(_as_list(tags.get("可蹭热点类型")), limit=10)

    # 事实清单：只写资料里有的
    facts = []
    if p.name:
        facts.append(f"商品名称：{p.name}")
    if p.brand:
        facts.append(f"品牌：{p.brand}")
    if p.category:
        facts.append(f"品类：{p.category}")
    if p.price is not None:
        facts.append(f"标价：{p.price:g} 元")
    if p.promotion:
        facts.append(f"促销：{p.promotion}")
    if p.brand_tone:
        facts.append(f"品牌调性：{p.brand_tone}")
    for k, v in (p.specs or {}).items():
        facts.append(f"规格·{k}：{v}")
    for x in _as_list(p.selling_points):
        facts.append(f"核心卖点：{x}")
    for x in _as_list(p.target_audience.get("年龄段")):
        facts.append(f"目标人群：{x}")
    for x in _as_list(p.target_audience.get("场景")):
        facts.append(f"使用场景：{x}")
    for x in evidence:
        facts.append(f"证据/背书：{x}")

    # 资料缺口：生成时会弱化，提示商家补全
    gaps = []
    if not (p.name or "").strip():
        gaps.append("商品名称")
    if p.price is None:
        gaps.append("标价")
    if not _as_list(p.selling_points):
        gaps.append("核心卖点")
    if not p.specs:
        gaps.append("规格参数")
    if not _as_list(p.target_audience):
        gaps.append("目标人群")
    gaps += _as_list(p.missing_fields)
    gaps = _dedup(gaps)

    allowed = _allowed_numbers(p)

    # 各平台切入角度（由已有事实推导，不新增信息）
    main_point = core_points[0] if core_points else (p.category or "核心卖点")
    main_scene = scenes[0] if scenes else "日常使用"
    main_pain = pain_points[0] if pain_points else "使用麻烦"
    angles = {
        "xiaohongshu": f"用「{main_scene}」的真实体验切入，把「{main_point}」翻译成省心/好用，结尾引导收藏",
        "bilibili": f"用参数与实测对比证明「{main_point}」，先给结论再给证据，适合测评/教程形式",
        "douyin": f"用「{main_pain}」做 3 秒痛点钩子，10 秒内演示「{main_point}」，结尾引导互动",
        "ecommerce": f"关键词前置，突出「{main_point}」与规格参数，用参数表+常见问题促成决策",
    }

    doc_lines = [
        f"商品：{p.name or '（未命名）'}"
        + (f"（品牌 {p.brand}）" if p.brand else "")
        + (f"，品类 {p.category}" if p.category else ""),
        f"标价：{p.price:g} 元" if p.price is not None else "标价：未填写",
        f"促销：{p.promotion}" if p.promotion else "促销：无",
        f"品牌调性：{p.brand_tone}" if p.brand_tone else "品牌调性：未填写",
        "核心卖点：" + ("；".join(core_points) if core_points else "（资料未填写，不要编造）"),
        "目标人群：" + ("；".join(audience["描述"]) if audience["描述"] else "（资料未填写）"),
        "使用场景：" + ("；".join(scenes) if scenes else "（资料未填写）"),
        "用户痛点：" + ("；".join(pain_points) if pain_points else "（资料未填写）"),
        "证据/背书：" + ("；".join(evidence) if evidence else "（无，不要声称有认证/专利/销量）"),
        "限制与禁忌：" + ("；".join(restrictions) if restrictions else "（未配置，按通用合规要求）"),
        "可蹭热点类型（选题参考，不是限制）：" + ("；".join(hot_topic_types) if hot_topic_types else "（未填写）"),
        "规格参数：" + (json.dumps(p.specs, ensure_ascii=False) if p.specs else "（未填写）"),
        "事实清单（文案只能出现这些事实）：",
        *[f"- {f}" for f in facts],
        "允许出现的数字：" + ("、".join(allowed) if allowed else "（无）"),
        "资料缺口（不要编造，可弱化或回避）：" + ("、".join(gaps) if gaps else "无"),
    ]

    return {
        "product_id": p.id,
        "name": p.name,
        "brand": p.brand,
        "category": p.category,
        "price": p.price,
        "promotion": p.promotion,
        "brand_tone": p.brand_tone,
        "core_points": core_points,
        "audience": audience,
        "scenes": scenes,
        "pain_points": pain_points,
        "evidence": evidence,
        "restrictions": restrictions,
        "hot_topic_types": hot_topic_types,
        "facts": facts,
        "numbers": allowed,
        "gaps": gaps,
        "angles": angles,
        "doc": "\n".join(doc_lines),
    }


def analyze_product(p: Product) -> dict:
    """公开的生成前分析（规则化，零大模型调用，秒回）。"""
    return build_product_brief(p)


# ---------------------------------------------------------------------------
# 事实校验（防 AI 幻觉）
# ---------------------------------------------------------------------------


def _text_of(content: ContentGenerate) -> str:
    parts = [content.title, content.body, content.cta]
    parts += [str(x) for x in (content.tags or [])]
    for step in content.script or []:
        if isinstance(step, dict):
            parts.append(str(step.get("text", "")))  # note 是拍摄提示，不算宣传表述
    return "\n".join(x for x in parts if x)


def fact_check(content: ContentGenerate, product: Product, brief: dict) -> dict:
    """程序化事实校验：数字白名单、成本泄露、无依据宣称、商品名命中。"""
    text = _text_of(content)
    issues: list = []
    allowed = set(brief.get("numbers") or [])

    # 1) 数字必须来自资料
    for m in _NUM_RE.finditer(text):
        raw, unit = m.group(1), (m.group(2) or "")
        if _norm_num(raw) in allowed:
            continue
        prefix = text[max(0, m.start() - 2):m.start()]
        is_money = unit in _MONEY_UNITS or "价" in prefix or "¥" in text[max(0, m.start() - 1):m.start() + 1]
        if is_money:
            issues.append({
                "level": "high",
                "msg": f"价格数字「{raw}{unit}」与商品资料不符（资料标价：{product.price}），疑似编造",
            })
        else:
            issues.append({
                "level": "warn",
                "msg": f"数字「{raw}{unit}」在商品资料中找不到，请核对是否编造",
            })

    # 2) 成本泄露
    if product.cost_price is not None:
        for tok in _numbers_in(product.cost_price):
            if re.search(rf"(?<![\d.]){re.escape(tok)}(?![\d])", text):
                issues.append({
                    "level": "high",
                    "msg": f"疑似泄露进货成本「{tok}」，宣传文案不应出现成本价",
                })

    # 3) 无依据的宣称
    evidence_text = " ".join(_as_list(product.evidence))
    for w in _CLAIM_WORDS:
        if w in text and w not in evidence_text:
            issues.append({
                "level": "high",
                "msg": f"宣称「{w}」但商品资料证据里没有依据",
            })

    # 4) 是否贴合商品（命中商品名/品类关键词）
    name = (product.name or "").strip()
    hit = False
    if name:
        keys = [name] + ([name[:4]] if len(name) >= 4 else [])
        hit = any(k and k in text for k in keys)
    if not hit and (product.category or "").strip():
        hit = product.category.strip() in text
    if not hit:
        issues.append({
            "level": "warn",
            "msg": "内容未出现商品名/品类关键词，可能不够贴合产品",
        })

    high = [i for i in issues if i["level"] == "high"]
    return {
        "passed": not high,
        "issues": issues,
        "high_count": len(high),
        "warn_count": len(issues) - len(high),
        "allowed_numbers": sorted(allowed)[:40],
    }


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------


def _product_context(p: Product) -> str:
    return (
        f"商品名称：{p.name}\n品牌：{p.brand}\n品类：{p.category}\n"
        f"规格：{json.dumps(p.specs, ensure_ascii=False)}\n"
        f"价格：{p.price}\n成本：{p.cost_price}\n"
        f"卖点：{json.dumps(p.selling_points, ensure_ascii=False)}\n"
        f"目标用户：{json.dumps(p.target_audience, ensure_ascii=False)}\n"
        f"品牌调性：{p.brand_tone}\n促销：{p.promotion}\n"
        f"证据：{json.dumps(p.evidence, ensure_ascii=False)}\n"
        f"限制：{json.dumps(p.restrictions, ensure_ascii=False)}"
    )


def _hard_limits(pcfg: dict) -> str:
    keys = ("name", "type", "tone", "strategy", "title_max_length", "tags_max",
            "cta_style", "emoji", "forbidden_words", "structures")
    return json.dumps({k: pcfg.get(k) for k in keys}, ensure_ascii=False)


def _build_prompt(p: Product, brief: dict, pcfg: dict, platform: str, content_type: str,
                  correction: list = None):
    skill = load_platform_skill(platform)
    system = GEN_SYSTEM
    if skill:
        system += "\n\n# 平台宣传 Skill（必须遵守）\n" + skill
    system += "\n\n# 平台硬性限制\n" + _hard_limits(pcfg)

    user = f"""# 产品分析（生成前必读，只能使用这里的事实）
{brief['doc']}

# 本次任务
平台：{pcfg['name']}（{pcfg['type']}）
内容形式：{content_type}
平台切入角度建议：{brief['angles'].get(platform, '')}

# 写作要求
1. 先在心里完成产品分析（卖给谁、解决什么痛点、最有说服力的证据是什么），再动笔。
2. 严格按「平台宣传 Skill」的标题公式、开头钩子、结构与红线写；标题不超过 {pcfg.get('title_max_length', 60)} 字。
3. 只能使用「事实清单」里的事实；数字只能来自「允许出现的数字」；资料缺口不要硬编。
4. 不要出现进货成本、利润率等内部数据；不要写"亲测有效"等无法验证的背书。
5. 若内容形式是 "script"（短视频/分镜脚本）：script 必须是 6~8 个分镜的数组；每个分镜的 scene 字段写这一段的作用或画面（如「痛点开场」「产品出场」「细节特写」「实测对比」「结论」「互动引导」），不要只写序号 1/2/3；note 里写景别/运镜/字幕/时长；body 只写 2~3 句总述，不要长篇大论。
6. 交稿前逐条自查「去 AI 味·五条硬规则」：有没有冒号词条堆叠、是不是只讲 1 个主卖点、有没有第一人称与具体场景、句子像不像人说话、结尾有没有硬喊购买。任何一条不合格就重写到合格为止。"""

    if correction:
        user += "\n\n# 上一版的问题（必须修正）\n" + "\n".join(f"- {x}" for x in correction)
        user += "\n请重写一版，避免上述问题；其余要求不变。"

    user += f"\n\n本次生成的内容形式（content_type）：{content_type}"
    return system, user


def generate_content(p: Product, platform: str, content_type: str):
    """返回 (ContentGenerate, brief, fact_check_result)。"""
    pcfg = get_platform_config(platform)
    brief = build_product_brief(p)

    if llm.is_mock:
        result = _mock_content(p, pcfg, content_type)
        fact = fact_check(result, p, brief)
        return result, brief, fact

    system, user = _build_prompt(p, brief, pcfg, platform, content_type)
    result = llm.complete_json(system, user, ContentGenerate)
    fact = fact_check(result, p, brief)

    # 严重问题（编造价格/成本泄露/无依据宣称）→ 带着问题清单重写一次
    if not fact["passed"]:
        high = [i["msg"] for i in fact["issues"] if i["level"] == "high"]
        try:
            system2, user2 = _build_prompt(p, brief, pcfg, platform, content_type, correction=high)
            retry = llm.complete_json(system2, user2, ContentGenerate)
            fact2 = fact_check(retry, p, brief)
            if fact2["high_count"] <= fact["high_count"]:
                result, fact = retry, fact2
        except Exception:  # 重试失败就保留第一版并标记问题
            pass

    return result, brief, fact


def _emoji(s: str, i: int, use: bool) -> str:
    return f"{EMOJIS[i % len(EMOJIS)]} {s}" if use else s


def _mock_content(p: Product, pcfg: dict, content_type: str) -> ContentGenerate:
    """规则化生成：演示用，基于真实商品字段拼装。"""
    points = p.selling_points or ["高品质", "实用设计"]
    name = p.name or "商品"
    tags = [t for t in ([p.category] + (p.target_audience.get("场景") or [])) if t][: pcfg.get("tags_max", 5) or 5]
    use_emoji = bool(pcfg.get("emoji"))
    cta = pcfg.get("cta_style", "关注我获取更多好物")

    if content_type == "script":
        p1 = points[0] if points else "核心卖点"
        p2 = points[1] if len(points) > 1 else "细节设计"
        p3 = points[2] if len(points) > 2 else "使用体验"
        script = [
            {"scene": "开场钩子", "text": _emoji(f"你是不是也在为这个头疼？今天给你看 {name}。", 0, use_emoji),
             "note": "产品特写，前 3 秒抓住注意力，配醒目字幕"},
            {"scene": "痛点场景", "text": _emoji("先说一个你一定会遇到的场景……", 1, use_emoji),
             "note": "实拍生活场景，交代使用痛点，增强代入感"},
            {"scene": "卖点演示 1", "text": _emoji(f"第一个亮点：{p1}。", 2, use_emoji),
             "note": "细节特写 + 手势引导，展示卖点"},
            {"scene": "卖点演示 2", "text": _emoji(f"再看这里，{p2}。", 3, use_emoji),
             "note": "中景演示，给出实际使用效果"},
            {"scene": "卖点演示 3", "text": _emoji(f"还有 {p3}，用过才知道。", 4, use_emoji),
             "note": "对比演示或口播补充，强化差异化"},
            {"scene": "行动引导", "text": _emoji("喜欢的话点个赞，评论区告诉我你的想法。", 5, use_emoji),
             "note": "口播 + 字幕引导点赞评论关注，出镜收尾"},
        ]
        title = _emoji(f"{name}，{p1}", 0, use_emoji)
        return ContentGenerate(title=title, tags=tags, cta=cta, script=script)

    title = _emoji(f"{name} | {'、'.join(points[:2])}", 0, use_emoji)[: pcfg.get("title_max_length", 60)]
    body_lines = [_emoji(f"{x}", i + 1, use_emoji) for i, x in enumerate(points)]
    if p.specs:
        body_lines.append("规格：" + "，".join(f"{k}{v}" for k, v in list(p.specs.items())[:5]))
    body = "\n".join(body_lines)
    return ContentGenerate(title=title, body=body, tags=tags, cta=cta)
