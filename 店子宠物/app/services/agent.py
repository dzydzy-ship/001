# -*- coding: utf-8 -*-
"""促销利润防护 Agent：一句话进来，一整套账单出去。

四步，每步都有刹车：

    ① 抽取   —— 大模型只把话拆成参数，不产生任何数字
    ② 追问   —— 参数不齐就只追问，**不调引擎**
    ③ 测算   —— 参数齐了才调 pricing.run()，拿到逐项拆解
    ④ 归因   —— 把拆解塞进提示词，模型只负责措辞；数字越界就回退模板
    ⑤ 拦截   —— 涉及改价/发券/改活动，一律拒绝，回到后台人工确认

设计上的关键取舍：**宁可回退到模板，也不让模型编数字**。
narration 生成后会做一次"数字溯源校验"，凡出现引擎里没有的金额，
直接丢弃这段文本、改用模板解释，并在返回体里标 fallback=True。
"""
import json
import re
from typing import Dict, List, Optional, Tuple

from ..config import load_yaml
from ..llm import llm
from ..models import AgentSession, Product, ProfitCheck, PromoConfig
from ..schemas import AgentPlan
from . import agent_skill, pricing

CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
          "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

# 明显是在"下指令"的说法，命中即拒绝
_WRITE_PATTERN = re.compile(
    r"(改价|调价|价格?[^，。；!？]{0,6}?(改|调)(到|成|为)|改库存|上架|下架|"
    r"(发布|创建|新建|删除|修改)(一个)?(活动|优惠|促销)|"
    r"(发|送|上)(一张|一个|个)?(无门槛)?(优惠)?券)"
)
# 出现这些词，说明是在"设想/测算"，不是在下指令
_PLANNING = re.compile(r"(打算|计划|准备|想|如果|假设|模拟|测算|算|会亏|亏不亏|利润|试试|看看)")
# 否定说法（不发券、别改价）不是指令，必须排除；但"能不能/可不可以"这种问句不能算否定
_NEGATED_WRITE = re.compile(
    r"(不|别|不要|不用|暂不|先不|取消|撤|去掉|停止)[^，。；]{0,6}"
    r"(改价|调价|改到|改成|改一下|上调|下调|发券|上架|下架|删除|发布活动|建活动)"
)
_NO_PROMO = re.compile(r"(不|没|无)(做)?(任何)?(促销|优惠|活动)")
# 券被取消：既支持"不发券"，也支持"把券去掉"
_NEGATED_COUPON = re.compile(
    r"(券|优惠券)[^，。；]{0,6}(去掉|取消|撤回|不发|别发|不用|不要)"
    r"|(不|别|取消|撤|去掉)[^，。；]{0,6}(券|优惠)"
)

_COUPON_PATTERNS = [
    re.compile(r"(\d+(?:\.\d+)?)\s*元\s*(?:的)?\s*(?:无门槛)?\s*券"),
    re.compile(r"(?:券|优惠券)[^0-9]{0,6}?(\d+(?:\.\d+)?)\s*元"),
    re.compile(r"满\s*\d+(?:\.\d+)?\s*减\s*(\d+(?:\.\d+)?)"),
]
_TIER_PATTERN = re.compile(
    r"(?:满|买|拍|下|做|上)?\s*([一二两三四五六七八九]|\d+)\s*件\s*(?:以上)?\s*"
    r"([一二三四五六七八九])([一二三四五六七八九])?\s*折"
)
_DISCOUNT_ONLY = re.compile(
    r"打\s*(?:个|一下|一)?\s*([一二三四五六七八九\d])([一二三四五六七八九])?\s*折"
)
_QTY_PATTERN = re.compile(r"(?:买|拍|下|来)\s*(\d+)\s*件|(\d+)\s*件(?:起|包邮)?")
_PRICE_PATTERN = re.compile(r"(?:标价|售价|定价|卖)\s*(?:是|为|改成|到)?\s*(\d+(?:\.\d+)?)")
_COST_PATTERN = re.compile(
    r"(?:进价|进货价|成本价?|出厂价|拿货价?|采购价)\s*(?:是|为|按|在)?\s*(\d+(?:\.\d+)?)"
    r"|按\s*(\d+(?:\.\d+)?)\s*(?:元)?\s*算"
)
_FREIGHT_INTL = re.compile(r"(?:国际|跨国|海外)\s*运费\s*(?:是|为|按)?\s*(\d+(?:\.\d+)?)")
_FREIGHT_DOM = re.compile(r"(?<!国际)(?<!跨国)(?<!海外)运费\s*(?:是|为|按|涨到|变成|算)?\s*(\d+(?:\.\d+)?)")


# ---------------------------------------------------------------- 配置
_POLICY_CACHE: Optional[dict] = None


def policy() -> dict:
    global _POLICY_CACHE
    if _POLICY_CACHE is None:
        _POLICY_CACHE = load_yaml("pricing_policy.yaml")
    return _POLICY_CACHE


def floor_pct(db=None) -> float:
    """毛利率红线：优先取库里默认口径，其次取 YAML。"""
    config = default_config(db) if db is not None else None
    if config is not None and config.profit_floor_pct is not None:
        return float(config.profit_floor_pct)
    return float(policy().get("profit_floor_pct", 5.0))

def floor_for_product(db, product=None, config=None) -> float:
    """这款商品的毛利率红线：商品档案 > 全局口径 > YAML 默认。

    A 方案的核心：薄利款和厚利款的底线本来就不该一样。
    """
    if product is not None and db is not None:
        try:
            from .costs import get_cost
            cost = get_cost(db, product.id) or {}
        except Exception:
            cost = {}
        if cost.get("profit_floor_pct") is not None:
            return float(cost["profit_floor_pct"])
    if config is not None and config.profit_floor_pct is not None:
        return float(config.profit_floor_pct)
    return float(policy().get("profit_floor_pct", 5.0))


def store_scan(db, qty: int = 2, tier_rate: float = 0.8, coupon: float = 0.0) -> Dict:
    """C 方案：同一个活动铺到全店，逐款算一遍，按毛利率排序。

    每款返回：单件成交价、利润、毛利率、自己的红线、还能让多少价（headroom）。
    """
    config = default_config(db)
    products = db.query(Product).all()
    items: List[Dict] = []

    for product in products:
        limit = floor_for_product(db, product, config)
        base = {
            "product_id": product.id,
            "name": product.name,
            "price": getattr(product, "price", None),
            "floor_pct": limit,
        }
        inp, missing = build_input(product, {}, config, AgentPlan(), db=db)
        if missing:
            base.update({
                "status": "missing",
                "missing": missing,
                "hint": "缺进货成本，先补成本档案再体检",
            })
            items.append(base)
            continue
        data = inp.as_dict()
        data.update({"qty": qty, "tier_rate": tier_rate, "coupon": coupon})
        try:
            result = pricing.run(pricing.PricingInput(**data))
        except pricing.PricingError as exc:
            base.update({"status": "error", "hint": f"参数不合法：{exc}"})
            items.append(base)
            continue

        single = pricing.PricingInput(**{**inp.as_dict(), "qty": 1,
                                        "tier_rate": 1.0, "coupon": 0.0})
        try:
            need = pricing.gmv_required(single, limit / 100.0)
        except pricing.PricingError:
            need = None
        unit_price = round(result["单件成交价"], 2)
        min_price = round(need, 2) if need is not None else None
        base.update({
            "status": "risk" if result["利润率"] < limit else "ok",
            "unit_price": unit_price,
            "profit": result["利润"],
            "margin": result["利润率"],
            "min_price": min_price,
            "headroom": round(unit_price - min_price, 2) if min_price is not None else None,
            "cost": inp.unit_cost,
            "bill": result,
        })
        items.append(base)

    def sort_key(item):
        margin = item.get("margin")
        return (margin is None, -(margin or 0))

    # 能吃折扣的排前面，缺成本的沉底
    items.sort(key=sort_key)

    ok = sum(1 for i in items if i["status"] == "ok")
    risk = sum(1 for i in items if i["status"] == "risk")
    missing = sum(1 for i in items if i["status"] in ("missing", "error"))
    return {
        "activity": {"qty": qty, "tier_rate": tier_rate, "coupon": coupon},
        "items": items,
        "summary": {"ok": ok, "risk": risk, "missing": missing, "total": len(items)},
        "text": f"同一个活动铺全店：{ok} 款还能赚、{risk} 款跌破红线"
                + (f"、{missing} 款缺成本" if missing else "") + "。",
    }



def default_config(db) -> Optional[PromoConfig]:
    """取默认口径；库里没有就按 YAML 现造一条（不落库）。"""
    if db is None:
        return None
    row = db.query(PromoConfig).filter(PromoConfig.is_default.is_(True)).first()
    if row:
        return row
    row = db.query(PromoConfig).first()
    if row:
        return row
    defaults = policy().get("defaults", {})
    return PromoConfig(
        name="YAML 默认口径",
        platform="taobao",
        commission_rate=defaults.get("commission_rate", 0.06),
        commission_base=defaults.get("commission_base", "gmv_after_coupon"),
        ship_fee=defaults.get("ship_fee", 19.9),
        freight_dom=defaults.get("freight_dom", 19.9),
        freight_cover=defaults.get("freight_cover", 0.0),
        coupon_borne_by=defaults.get("coupon_borne_by", "merchant"),
        tier_discounts=defaults.get("tier_discounts", {"2": 0.8}),
        profit_floor_pct=float(policy().get("profit_floor_pct", 5.0)),
    )


def _config_kwargs(config: Optional[PromoConfig]) -> Dict:
    if config is None:
        defaults = policy().get("defaults", {})
        return {
            "commission": defaults.get("commission_rate", 0.06),
            "commission_base": defaults.get("commission_base", "gmv_after_coupon"),
            "ship_fee": defaults.get("ship_fee", 19.9),
            "freight_dom": defaults.get("freight_dom", 19.9),
            "freight_cover": defaults.get("freight_cover", 0.0),
            "freight_intl": defaults.get("freight_intl", 0.0),
        }
    return {
        "commission": config.commission_rate,
        "commission_base": config.commission_base,
        "ship_fee": config.ship_fee,
        "freight_dom": config.freight_dom,
        "freight_cover": config.freight_cover,
        "freight_intl": 0.0,
    }


# ---------------------------------------------------------------- ① 抽取
def match_product(db, message: str, product_id: Optional[int] = None) -> Optional[Product]:
    """从这句话里认出是哪款商品：先看话里点了谁，再退回上一轮的商品。"""
    products = db.query(Product).all()
    text = message.upper()
    best, best_len = None, 0
    for p in products:
        raw = (p.name or "") + " " + (p.brand or "")
        # ① 整词切分（中文品牌/品类名）
        tokens = [t.strip() for t in re.split(r"[\s,，、·/（）()]+", raw)]
        # ② 型号片段：SR-72、K270、FX9672 这类，往往才是用户口头说的那个
        tokens += re.findall(r"[A-Za-z]{1,6}-?\d{2,6}", raw)
        for token in tokens:
            if len(token) >= 2 and token.upper() in text and len(token) > best_len:
                best, best_len = p, len(token)
    if best is not None:
        return best
    if product_id:
        return db.get(Product, product_id)
    return None


def parse_plan(message: str) -> AgentPlan:
    """规则解析（Mock 模式与兜底共用）。只拆参数，绝不算钱。"""
    plan = AgentPlan()
    text = message

    if _WRITE_PATTERN.search(text) and not _PLANNING.search(text) and not _NEGATED_WRITE.search(text):
        plan.intent = "write_action"
        return plan

    if _NO_PROMO.search(text):
        # "不促销的话利润多少" —— 明确要求回到无优惠状态
        plan.coupon = 0.0
        plan.tier_rate = 1.0
        plan.qty = 1

    for pattern in _COUPON_PATTERNS:
        m = pattern.search(text)
        if m:
            plan.coupon = float(m.group(1))
            break
    if _NEGATED_COUPON.search(text):
        plan.coupon = 0.0

    m = _TIER_PATTERN.search(text)
    if m:
        tier_raw = m.group(2) + (m.group(3) or "")
        rate = _tier_to_rate(tier_raw)
        if rate:
            plan.tier_rate = rate
            qty = CN_NUM.get(m.group(1)) if m.group(1) in CN_NUM else _to_int(m.group(1))
            plan.qty = max(int(qty or 2), 2)
    else:
        m = _DISCOUNT_ONLY.search(text)
        if m:
            rate = _tier_to_rate(m.group(1) + (m.group(2) or ""))
            if rate:
                plan.tier_rate = rate

    if plan.qty is None:
        m = _QTY_PATTERN.search(text)
        if m:
            plan.qty = int(m.group(1) or m.group(2))

    m = _PRICE_PATTERN.search(text)
    if m:
        plan.price = float(m.group(1))

    m = _COST_PATTERN.search(text)
    if m:
        plan.unit_cost = float(m.group(1) or m.group(2))

    m = _FREIGHT_INTL.search(text)
    if m:
        plan.freight_intl = float(m.group(1))
    else:
        m = _FREIGHT_DOM.search(text)
        if m:
            plan.freight_dom = float(m.group(1))

    return plan


def _tier_to_rate(raw: str) -> Optional[float]:
    """八折 → 0.8；七五折 → 0.75；9折 → 0.9。"""
    digits = []
    for ch in raw:
        if ch in CN_NUM:
            digits.append(CN_NUM[ch])
        elif ch.isdigit():
            digits.append(int(ch))
    if not digits:
        return None
    if len(digits) == 1:
        zhe = float(digits[0])
    else:
        zhe = int("".join(str(d) for d in digits)) / 10.0
    if not (0 < zhe <= 10):
        return None
    return round(zhe / 10, 3)


def _to_int(raw: str) -> Optional[int]:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def plan_with_llm(message: str, state: Dict) -> AgentPlan:
    """有 Key 时用模型做语义抽取（结构化输出，仍然不产生数字）。"""
    system = (
        "你是促销测算系统的参数解析器。你的唯一任务是把商家的话拆成结构化参数，"
        "绝对不要计算任何金额，不要推测缺失的成本，不要给建议。"
        "如果某参数没有明说，就留空。intent 只能取 price_check / write_action / chitchat："
        "商家要求你直接改价、发券、改活动配置时取 write_action。"
    )
    user = f"当前已知参数：{json.dumps(state, ensure_ascii=False)}\n商家这句话：{message}"
    return llm.complete_json(system, user, AgentPlan, temperature=0.1)


# ---------------------------------------------------------------- ③ 测算
def build_input(product: Optional[Product], state: Dict, config,
                plan: AgentPlan, db=None) -> Tuple[pricing.PricingInput, List[str]]:
    """把商品档案 + 会话状态 + 本轮解析结果合成引擎入参，并列出缺失项。

    取值优先级：本轮这句话 > 会话状态 > 商品成本档案(product_costs) > Product.cost_price > 全局口径。
    """
    cfg = _config_kwargs(config)
    sku = product.name if product else state.get("sku", "")
    cost: Dict = {}
    if db is not None and product is not None:
        try:
            from .costs import get_cost
            cost = get_cost(db, product.id) or {}
        except Exception:
            cost = {}

    price = _first(plan.price, state.get("price"), getattr(product, "price", None))
    unit_cost = _first(plan.unit_cost, state.get("unit_cost"),
                       cost.get("unit_cost"), getattr(product, "cost_price", None))
    freight_intl = _first(plan.freight_intl, state.get("freight_intl"),
                          cost.get("freight_intl"), cfg["freight_intl"])
    freight_dom = _first(plan.freight_dom, state.get("freight_dom"),
                         cost.get("freight_dom"), cfg["freight_dom"])
    qty = _first(plan.qty, state.get("qty"), 1)
    tier_rate = _first(plan.tier_rate, state.get("tier_rate"), 1.0)
    coupon = _first(plan.coupon, state.get("coupon"), 0.0)

    inp = pricing.PricingInput(
        sku=sku or "",
        price=price,
        qty=int(qty or 1),
        unit_cost=unit_cost,
        freight_intl=float(freight_intl or 0.0),
        freight_dom=float(freight_dom or 0.0),
        freight_cover=cfg["freight_cover"],
        tier_rate=float(tier_rate or 1.0),
        coupon=float(coupon or 0.0),
        commission=cfg["commission"],
        commission_base=cfg["commission_base"],
        ship_fee=cfg["ship_fee"],
    )
    missing: List[str] = []
    if inp.price is None:
        missing.append("price")
    if inp.unit_cost is None:
        missing.append("unit_cost")
    return inp, missing


def _first(*values):
    for value in values:
        if value is not None:
            return value
    return None


def ladder(inp: pricing.PricingInput) -> List[Dict]:
    """四行阶梯：不促销 / 只发券 / 只打折 / 叠加。

    归因不靠"分摊"这种含糊说法，而是把四个方案都算一遍，
    差额就是每项优惠真实吃掉的利润——可复算、可当场核对。
    """
    rows = []

    def add(name: str, patch: Dict) -> None:
        data = inp.as_dict()
        data.update(patch)
        try:
            r = pricing.run(pricing.PricingInput(**data))
        except pricing.PricingError:
            return
        rows.append({"方案": name, "成交额": r["成交额"], "利润": r["利润"],
                     "利润率": r["利润率"]})

    add("不促销（单件）", {"qty": 1, "tier_rate": 1.0, "coupon": 0.0})
    if inp.coupon > 0:
        add("只发券", {"qty": 1, "tier_rate": 1.0})
    if inp.tier_rate < 1:
        add(f"只做{_qty_label(inp.qty)}折扣", {"qty": inp.qty, "tier_rate": inp.tier_rate,
                                              "coupon": 0.0})
    if inp.coupon > 0 or inp.tier_rate < 1:
        add("叠加（当前方案）", {"qty": inp.qty, "tier_rate": inp.tier_rate})
    return rows


def _qty_label(qty) -> str:
    names = {1: "单件", 2: "两件", 3: "三件", 4: "四件"}
    return names.get(int(qty or 1), f"{qty}件")


# ---------------------------------------------------------------- ④ 归因
def narrate(result: Dict, options: Dict, floor: float, inp: pricing.PricingInput,
            ladder_rows: List[Dict]) -> str:
    """模板解释：所有数字都来自引擎，句式固定，可离线演示。"""
    risk = result["利润率"] < floor
    parts: List[str] = []

    plan_name = _plan_name(inp)
    parts.append(
        f"按「{plan_name}」算：{result['件数']} 件成交额 {_money(result['成交额'])} 元"
        f"（买家另付运费 {_money(result['买家运费'])}），"
        f"扣商品成本 {_money(result['商品成本'])}、国内运费 {_money(result['国内运费'])}、"
        f"平台佣金 {_money(result['平台佣金'])}，这单剩 {_money(result['利润'])} 元，"
        f"毛利率 {result['利润率']}%。"
    )

    if risk:
        gap = round(floor - result["利润率"], 1)
        parts.append(f"比 {floor}% 的红线低了 {gap} 个百分点，建议先别发布。")

    # 阶梯归因：差额来自引擎复算，不是分数摊派
    base = next((r for r in ladder_rows if r["方案"].startswith("不促销")), None)
    tier_row = next((r for r in ladder_rows if r["方案"].startswith("只做")), None)
    coupon_row = next((r for r in ladder_rows if r["方案"] == "只发券"), None)

    if tier_row and base:
        delta = round(tier_row["利润"] - base["利润"], 2)
        parts.append(f"分摊开看：只做{_qty_label(inp.qty)}折扣一项，利润就从 "
                     f"{_money(base['利润'])} 变成 {_money(tier_row['利润'])} 元"
                     f"（{_money(delta)}）。")
    if coupon_row and tier_row:
        delta = round(result["利润"] - tier_row["利润"], 2)
        parts.append(f"再叠这张券，又多亏 {_money(abs(delta))} 元；券不是主因，"
                     f"折扣已经把单件毛利压到 {_money(_unit_gap(result))} 元，佣金一扣就穿。")
    elif coupon_row and base and not tier_row:
        delta = round(coupon_row["利润"] - base["利润"], 2)
        parts.append(f"这张券单独让利润少了 {_money(abs(delta))} 元。")

    if result["警告"]:
        parts.append("另外：" + "；".join(result["警告"]) + "。")

    if risk:
        max_coupon = options["最多可发券"]
        if max_coupon > 0:
            parts.append(f"想保住 {floor}% 毛利率，这单最多只能发 {_money(max_coupon)} 元券。")
        elif options["可选调整"]:
            best = options["可选调整"][0]
            tail = ("券发多少都保不住" if inp.coupon > 0
                    else "券这张牌已经打不出来了，得先动折扣力度或标价")
            parts.append(f"按当前折扣结构，{tail}；最接近的是「{best['名称']}」，"
                         f"利润 {_money(best['利润'])} 元。")
    return "".join(parts)


def _unit_gap(result: Dict) -> float:
    """单件成交价减单件成本：折扣把毛利压到多少。"""
    qty = result["件数"] or 1
    return round(result["成交额"] / qty - result["商品成本"] / qty, 2)


def _plan_name(inp: pricing.PricingInput) -> str:
    bits = []
    if inp.tier_rate < 1:
        bits.append(f"{_qty_label(inp.qty)}{_rate_label(inp.tier_rate)}")
    if inp.coupon > 0:
        bits.append(f"{_money(inp.coupon)} 元券")
    return " + ".join(bits) if bits else "不促销"


def _rate_label(rate: float) -> str:
    value = round(rate * 10, 2)
    text = ("%g" % value)
    return f"{text}折"


def _money(value) -> str:
    text = "{:,.2f}".format(float(value))
    return text.replace("-", "−")


def narrate_with_llm(result: Dict, options: Dict, floor: float, inp: pricing.PricingInput,
                     ladder_rows: List[Dict], message: str) -> Tuple[Optional[str], bool]:
    """让模型只负责措辞。返回 (文本, 是否因数字越界回退)。"""
    if llm.is_mock:
        return None, False

    facts = {k: v for k, v in result.items() if k not in ("入参", "警告")}
    facts["毛利红线"] = floor
    facts["最多可发券"] = options["最多可发券"]
    facts["阶梯"] = ladder_rows
    facts["入参"] = result["入参"]

    system = (
        "你是商家侧的促销利润顾问。规则：\n"
        "1. 只允许使用 facts 里出现过的数字，一个都不许新造、不许换算；\n"
        "2. 不要给改价、发券、改活动的操作结论（那是商家自己的动作）；\n"
        "3. 先说结论（亏还是赚、离红线差多少），再给一句归因，最后给一条可操作建议；\n"
        "4. 中文，150 字以内，不要用 Markdown 标题。"
    )
    user = (f"商家原话：{message}\n\nfacts：{json.dumps(facts, ensure_ascii=False)}\n\n"
            f"请输出 narrative 字段。")

    try:
        plan = llm.complete_json(system, user, _NarrationSchema, temperature=0.3)
    except Exception:  # 网络/解析失败都退回模板，演示不能因此中断
        return None, False

    text = (plan.narrative or "").strip()
    if not text:
        return None, False
    if not _numbers_traceable(text, result, options, floor, inp):
        return None, True
    return text, False


try:  # pydantic 版本差异兼容：仅在能导入时定义
    from pydantic import BaseModel, Field

    class _NarrationSchema(BaseModel):
        narrative: str = Field(default="")

except Exception:  # pragma: no cover
    _NarrationSchema = None  # type: ignore


def _numbers_traceable(text: str, result: Dict, options: Dict, floor: float,
                       inp: pricing.PricingInput) -> bool:
    """数字溯源：解释里出现的每个"像金额"的数字，都必须在引擎输出里找得到。"""
    allowed = set()

    def add(value) -> None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            for digits in (0, 1, 2):
                allowed.add(round(float(value), digits))

    for value in result.values():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            add(value)
    add(floor)
    add(inp.coupon)
    add(inp.price)
    add(inp.unit_cost)
    add(inp.tier_rate * 10)
    add(inp.commission * 100)
    add(options.get("最多可发券", 0))
    # 引擎可推导的派生量也允许出现：离红线差几个点、单件毛利剩多少
    add(round(floor - result.get("利润率", 0), 1))
    add(round(result.get("成交额", 0) / (result.get("件数") or 1)
              - result.get("商品成本", 0) / (result.get("件数") or 1), 2))
    for option in options.get("可选调整", []):
        add(option.get("利润"))
        add(option.get("利润率"))

    for token in re.findall(r"\d+(?:\.\d+)?", text):
        value = float(token)
        if value <= 12 and "." not in token:   # 件数、百分比等小整数不苛刻要求
            continue
        if not any(abs(value - a) <= 0.011 for a in allowed):
            return False
    return True


# ---------------------------------------------------------------- ⑤ 拦截
def refusal() -> str:
    return policy().get("refusal", {}).get(
        "write_action",
        "改价、发券、修改活动属于店铺后台的写操作，我不能替你执行；"
        "我可以把改完之后的账算给你看。",
    )


def clarify_questions(missing: List[str]) -> List[Dict]:
    wording = policy().get("clarify", {})
    return [{"field": field, "question": wording.get(field, f"请提供 {field}")}
            for field in missing]


# ---------------------------------------------------------------- 主流程
def handle(db, session_key: str, message: str, product_id: Optional[int] = None) -> Dict:
    """一轮对话的全部逻辑。返回体直接给前端渲染。"""
    session = (db.query(AgentSession)
                 .filter(AgentSession.session_key == session_key)
                 .first())
    if session is None:
        session = AgentSession(session_key=session_key, state={}, turns=0)
        db.add(session)
        db.commit()
        db.refresh(session)

    state: Dict = dict(session.state or {})
    session.turns = (session.turns or 0) + 1

    product = match_product(db, message, product_id or state.get("product_id"))
    if product is not None:
        state["product_id"] = product.id
        state["sku"] = product.name
        session.product_id = product.id

    # 越权拦截先于模型：涉及改价/发券的请求，不管模型怎么想都先挡下
    rule_plan = parse_plan(message)
    if rule_plan.intent == "write_action":
        db.commit()
        return {
            "action": "refuse",
            "reply": refusal(),
            "session_key": session_key,
            "product": _product_brief(product),
        }

    # ⑥ 配了 Key 就交给真 Agent（模型自己选工具，数字全部来自引擎）；
    # 它会自己判断该不该追问；任何异常都退回下面的规则路径
    if not llm.is_mock:
        try:
            agent_result = agent_chat(db, session_key, message, product_id)
            if agent_result:
                return agent_result
        except Exception:
            pass

    # ① 抽取（规则结果已在上方算过，这里复用）
    plan = rule_plan
    if plan.intent != "write_action" and not llm.is_mock:
        try:
            llm_plan = plan_with_llm(message, state)
            if llm_plan.intent:
                plan.intent = llm_plan.intent
            for field in ("qty", "tier_rate", "coupon", "price", "unit_cost",
                          "freight_intl", "freight_dom"):
                value = getattr(llm_plan, field)
                if value is not None and getattr(plan, field) is None:
                    setattr(plan, field, value)
        except Exception:
            pass  # 解析失败就用规则结果，绝不因此中断对话

    # ⑤ 拦截（在调用引擎之前就挡住）
    if plan.intent == "write_action":
        db.commit()
        return {
            "action": "refuse",
            "reply": refusal(),
            "session_key": session_key,
            "product": _product_brief(product),
        }

    for field in ("qty", "tier_rate", "coupon", "price", "unit_cost",
                  "freight_intl", "freight_dom"):
        value = getattr(plan, field)
        if value is not None:
            state[field] = value

    config = default_config(db)
    limit = floor_for_product(db, product, config)

    # ③ 参数合成
    inp, missing = build_input(product, state, config, plan, db=db)
    if missing:
        state["missing"] = missing
        session.state = state
        db.commit()
        questions = clarify_questions(missing)
        ask = "；".join(q["question"] for q in questions)
        return {
            "action": "clarify",
            "reply": f"先不急着算——这里还差 {len(missing)} 个口径，需要你确认：{ask}",
            "clarify": questions,
            "session_key": session_key,
            "product": _product_brief(product),
        }

    session.state = state
    result = pricing.run(inp)
    options = pricing.suggest_options(inp, limit)
    ladder_rows = ladder(inp)

    # ④ 归因
    text = narrate(result, options, limit, inp, ladder_rows)
    fallback = False
    llm_text, fallback = narrate_with_llm(result, options, limit, inp, ladder_rows, message)
    if llm_text:
        text = llm_text

    verdict = "risk" if result["利润率"] < limit else "ok"

    db.add(ProfitCheck(
        session_key=session_key,
        product_id=product.id if product else None,
        sku=inp.sku,
        inputs=inp.as_dict(),
        result={k: v for k, v in result.items() if k != "入参"},
        options=options.get("可选调整", []),
        verdict=verdict,
        explanation=text,
    ))
    db.commit()

    return {
        "action": "bill",
        "reply": text,
        "fallback": fallback,
        "session_key": session_key,
        "verdict": verdict,
        "floor_pct": limit,
        "product": _product_brief(product),
        "params": {
            "件数": inp.qty,
            "折扣率": inp.tier_rate,
            "券额": inp.coupon,
            "标价": inp.price,
            "进货成本": inp.unit_cost,
        },
        "bill": {k: v for k, v in result.items() if k not in ("入参",)},
        "ladder": ladder_rows,
        "options": options.get("可选调整", []),
        "max_coupon": options.get("最多可发券"),
    }


def _product_brief(product: Optional[Product]) -> Optional[Dict]:
    if product is None:
        return None
    return {
        "id": product.id,
        "name": product.name,
        "price": product.price,
        "cost_price": product.cost_price,
    }


def reset_session(db, session_key: str) -> None:
    session = (db.query(AgentSession)
                 .filter(AgentSession.session_key == session_key)
                 .first())
    if session:
        session.state = {}
        session.product_id = None
        session.turns = 0
        db.commit()


def guard_product(db, product_id: int, qty: int = 2, tier_rate: float = 0.8,
                  coupon: float = 0.0) -> Dict:
    """给内容页用的一行结论：这款商品在指定促销下还赚不赚钱。"""
    product = db.get(Product, product_id)
    if product is None:
        raise ValueError("商品不存在")
    config = default_config(db)
    limit = floor_for_product(db, product, config)
    inp, missing = build_input(product, {}, config, AgentPlan(), db=db)
    if missing:
        return {"verdict": "unknown", "missing": missing,
                "text": "这款商品还缺成本口径，无法判断促销是否亏本。"}
    data = inp.as_dict()
    data.update({"qty": qty, "tier_rate": tier_rate, "coupon": coupon})
    try:
        result = pricing.run(pricing.PricingInput(**data))
    except pricing.PricingError as exc:
        return {"verdict": "unknown", "text": f"参数不合法：{exc}"}

    verdict = "risk" if result["利润率"] < limit else "ok"
    if verdict == "risk":
        text = (f"当前促销下利润 {_money(result['利润'])} 元"
                f"（毛利率 {result['利润率']}%，红线 {limit}%），建议先调整折扣。")
    else:
        text = (f"当前促销下利润 {_money(result['利润'])} 元"
                f"（毛利率 {result['利润率']}%），在红线以上。")
    return {"verdict": verdict, "bill": result, "text": text, "floor_pct": limit}


# ---------------------------------------------------------------- ⑥ 真 Agent（工具循环）
def _num(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_rate(value) -> Optional[float]:
    """把 0.8 / 8 / "0.8" / "八折" 统一成折扣率。"""
    rate = _num(value)
    if rate is None:
        return None
    if rate > 1.5:
        rate = rate / 10.0
    if rate <= 0 or rate > 1:
        return None
    return round(rate, 3)


def _promo_defaults(db) -> Dict:
    config = default_config(db)
    tier = getattr(config, "tier_discounts", None) or {}
    coupon = getattr(config, "default_coupon", None)
    return {
        "qty": 2,
        "tier": float(tier.get("2", 0.8)) if isinstance(tier, dict) else 0.8,
        "coupon": float(coupon if coupon is not None else 29.0),
    }


def _collect_numbers(obj, bag: set) -> None:
    """把工具返回里出现过的所有数字收进白名单。"""
    if isinstance(obj, dict):
        for value in obj.values():
            _collect_numbers(value, bag)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            _collect_numbers(value, bag)
    elif isinstance(obj, bool):
        return
    elif isinstance(obj, (int, float)):
        for digits in (0, 1, 2):
            bag.add(round(float(obj), digits))
            # 正文里的数字不带正负号（"亏 56.26"），所以绝对值也进白名单
            bag.add(round(abs(float(obj)), digits))
    elif isinstance(obj, str):
        for token in re.findall(r"\d+(?:\.\d+)?", obj):
            try:
                bag.add(round(float(token), 2))
            except ValueError:
                continue


def _reply_ok(text: str, bag: set) -> bool:
    """模型这句里出现的金额，必须在工具返回里找得到。"""
    for token in re.findall(r"\d+(?:\.\d+)?", text or ""):
        value = float(token)
        if value <= 12 and "." not in token:  # 件数、几个人这类小整数放宽
            continue
        if not any(abs(value - allowed) <= 0.011 for allowed in bag):
            return False
    return True


def _rate_label(rate) -> str:
    try:
        value = float(rate)
    except (TypeError, ValueError):
        return "原价"
    if abs(value - 1) < 1e-6:
        return "不打折"
    return f"{value * 10:g} 折"


def _tool_label(name: str, args: Dict, observation: Dict) -> str:
    args = args or {}
    if name == "calc_profit":
        product = (observation.get("product") or {}).get("name") or "未指定商品"
        parts = [product]
        if observation.get("params", {}).get("件数"):
            parts.append(f"{observation['params']['件数']} 件")
        parts.append(_rate_label((observation.get("params") or {}).get("折扣率")))
        coupon = (observation.get("params") or {}).get("券额")
        if coupon:
            parts.append(f"{coupon:g} 元券")
        return "测算 " + " · ".join(parts)
    if name == "store_scan":
        activity = observation.get("activity") or {}
        return (f"全店体检 {activity.get('qty', '')} 件 · "
                f"{_rate_label(activity.get('tier_rate'))} · {activity.get('coupon', 0):g} 元券")
    if name == "list_products":
        return f"查商品清单（{len(observation.get('products') or [])} 款）"
    if name == "get_policy":
        return "读计价口径"
    if name == "get_cost":
        return f"读成本档案（商品 {args.get('product_id')}）"
    return f"调用 {name}"


def run_tool(db, name: str, args: Optional[Dict] = None) -> Dict:
    """Agent 唯一的取数入口：所有数字都必须从这里出来。"""
    args = dict(args or {})
    name = (name or "").strip()

    if name == "list_products":
        rows = []
        for product in db.query(Product).all():
            try:
                from .costs import get_cost
                cost = get_cost(db, product.id) or {}
            except Exception:
                cost = {}
            rows.append({
                "id": product.id,
                "name": product.name,
                "price": product.price,
                "cost_price": product.cost_price,
                "cost_saved": cost.get("unit_cost"),
                "floor_pct": floor_for_product(db, product),
                "has_cost": bool(cost.get("unit_cost") or product.cost_price),
            })
        return {"products": rows}

    if name == "get_policy":
        return {"policy": policy(), "promo_defaults": _promo_defaults(db)}

    if name == "get_cost":
        pid = _num(args.get("product_id"))
        if pid is None:
            return {"error": "缺 product_id"}
        from .costs import get_cost
        row = get_cost(db, int(pid)) or {}
        return {"product_id": int(pid), "cost": row,
                "note": "空值表示这款还没有成本档案，需要先问商家进货价"}

    if name == "calc_profit":
        product = None
        raw_id = args.get("product_id")
        if raw_id is not None or args.get("sku"):
            product = match_product(db, str(args.get("sku") or ""),
                                    int(raw_id) if _num(raw_id) else None)
        state: Dict = {}
        for key in ("qty", "tier_rate", "coupon", "price", "unit_cost",
                    "freight_intl", "freight_dom"):
            if args.get(key) is not None:
                state[key] = args[key]
        if state.get("tier_rate") is not None:
            state["tier_rate"] = _coerce_rate(state["tier_rate"])
        if state.get("qty") is not None:
            qty = _num(state["qty"])
            state["qty"] = int(qty) if qty else None
        for key in ("coupon", "price", "unit_cost", "freight_intl", "freight_dom"):
            if state.get(key) is not None:
                state[key] = _num(state[key])

        config = default_config(db)
        inp, missing = build_input(product, state, config, AgentPlan(), db=db)
        if missing:
            return {"missing": missing, "product": _product_brief(product),
                    "hint": "这些口径还没确认，先问商家，不要猜"}
        try:
            result = pricing.run(inp)
        except pricing.PricingError as exc:
            return {"error": str(exc), "product": _product_brief(product)}

        limit = floor_for_product(db, product, config)
        options = pricing.suggest_options(inp, limit)
        rows = ladder(inp)
        verdict = "risk" if result["利润率"] < limit else "ok"
        return {
            "product": _product_brief(product),
            "params": {"件数": inp.qty, "折扣率": inp.tier_rate, "券额": inp.coupon,
                       "标价": inp.price, "进货成本": inp.unit_cost},
            "bill": {k: v for k, v in result.items() if k != "入参"},
            "verdict": verdict,
            "floor_pct": limit,
            "ladder": rows,
            "options": options.get("可选调整", []),
            "max_coupon": options.get("最多可发券"),
            "text": narrate(result, options, limit, inp, rows),
        }

    if name == "store_scan":
        qty = _num(args.get("qty")) or 2
        rate = _coerce_rate(args.get("tier_rate"))
        coupon = _num(args.get("coupon"))
        return store_scan(db, qty=int(qty),
                          tier_rate=rate if rate is not None else 1.0,
                          coupon=coupon or 0.0)

    return {"error": f"没有这个工具：{name}"}


def agent_chat(db, session_key: str, message: str,
               product_id: Optional[int] = None) -> Optional[Dict]:
    """真 Agent 的一轮：模型自己决定调哪个工具，数字全部来自引擎。

    返回 None 表示这次不接管（Mock 模式或结构没装好），由规则路径继续。
    """
    if llm.is_mock or agent_skill.AgentAction is None:
        return None

    session = (db.query(AgentSession)
                 .filter(AgentSession.session_key == session_key)
                 .first())
    if session is None:
        session = AgentSession(session_key=session_key, state={}, turns=0)
        db.add(session)
        db.commit()
        db.refresh(session)

    state: Dict = dict(session.state or {})
    session.turns = (session.turns or 0) + 1

    product = match_product(db, message, product_id or state.get("product_id"))
    if product is not None:
        state["product_id"] = product.id
        session.product_id = product.id

    known = {
        "当前正在看的商品": _product_brief(product),
        "上一轮确认过的参数": {k: state.get(k) for k in
                              ("qty", "tier_rate", "coupon", "price", "unit_cost",
                               "freight_intl", "freight_dom")
                              if state.get(k) is not None},
        "毛利红线": floor_for_product(db, product, default_config(db)),
    }

    system = agent_skill.system_prompt()
    user_msg = (f"商家原话：{message}\n\n"
                f"已知信息：{json.dumps(known, ensure_ascii=False)}")

    traces: List[Dict] = []
    bag: set = set()
    last_calc: Optional[Dict] = None
    last_scan: Optional[Dict] = None
    answer_text = ""
    seen_calls = set()

    for _step in range(agent_skill.MAX_STEPS):
        action = llm.complete_json(system, user_msg, agent_skill.AgentAction,
                                   temperature=0.2)
        act = (action.action or "").strip().lower()

        if act == "refuse":
            db.commit()
            return {
                "action": "refuse",
                "reply": (action.reply or "").strip() or refusal(),
                "session_key": session_key,
                "product": _product_brief(product),
                "trace": traces,
            }

        if act == "tool":
            signature = f"{action.tool}|{json.dumps(action.args or {}, ensure_ascii=False, sort_keys=True)}"
            if signature in seen_calls:
                # 同一个工具同样的参数连着调，说明它卡住了：别再空转，直接收口
                break
            seen_calls.add(signature)
            observation = run_tool(db, action.tool, action.args)
            _collect_numbers(observation, bag)
            traces.append({
                "tool": action.tool,
                "label": _tool_label(action.tool, action.args or {}, observation),
                "error": observation.get("error"),
            })
            if observation.get("bill"):
                last_calc = observation
            if observation.get("items"):
                last_scan = observation
            user_msg += (f"\n\n[工具 {action.tool} 的返回]\n"
                         f"{json.dumps(observation, ensure_ascii=False)[:3000]}")
            continue

        if act == "ask":
            asked = [str(q).strip() for q in (action.ask or []) if str(q).strip()]
            if not asked:
                asked = ["先说一下：这款的进货价和标价分别是多少？"]
            state["missing"] = asked
            session.state = state
            db.commit()
            head = (action.reply or "").strip() or "还差几个口径，确认一下再算："
            return {
                "action": "clarify",
                "reply": head,
                "clarify": [{"field": "", "question": q} for q in asked],
                "session_key": session_key,
                "product": _product_brief(product),
                "trace": traces,
            }

        answer_text = (action.reply or "").strip()
        break

    # ---- 组装结论 ----
    if last_calc:
        inp = last_calc.get("input")
        params = last_calc.get("params", {})
        text = answer_text
        fallback = False
        if not text or not _reply_ok(text, bag):
            fallback = True
            text = last_calc.get("text") or "这套配置我算完了，看下面的账单。"
        for key, value in (("qty", params.get("件数")), ("tier_rate", params.get("折扣率")),
                           ("coupon", params.get("券额")), ("price", params.get("标价")),
                           ("unit_cost", params.get("进货成本"))):
            if value is not None:
                state[key] = value
        session.state = state
        db.add(ProfitCheck(
            session_key=session_key,
            product_id=(last_calc.get("product") or {}).get("id"),
            sku=(last_calc.get("product") or {}).get("name") or "",
            inputs=params,
            result=last_calc.get("bill") or {},
            options=last_calc.get("options") or [],
            verdict=last_calc.get("verdict"),
            explanation=text,
        ))
        db.commit()
        return {
            "action": "bill",
            "reply": text,
            "fallback": fallback,
            "session_key": session_key,
            "verdict": last_calc.get("verdict"),
            "floor_pct": last_calc.get("floor_pct"),
            "product": last_calc.get("product"),
            "params": params,
            "bill": last_calc.get("bill"),
            "ladder": last_calc.get("ladder"),
            "options": last_calc.get("options"),
            "max_coupon": last_calc.get("max_coupon"),
            "trace": traces,
        }

    if last_scan is not None:
        # 全店体检：表格用引擎的 items 渲染，正文用它自己给的结论句
        text = answer_text if answer_text and _reply_ok(answer_text, bag) else last_scan.get("text", "")
        session.state = state
        db.commit()
        return {
            "action": "talk",
            "reply": text,
            "fallback": False,
            "session_key": session_key,
            "product": _product_brief(product),
            "trace": traces,
            "scan": last_scan,
        }

    text = answer_text
    fallback = False
    if not text:
        text = "我在。你把商品和想上的活动说一下，我调引擎算给你看。"
        fallback = True
    elif not _reply_ok(text, bag):
        text = "这个我不敢口头报数——把商品和促销组合说清楚，我用引擎算完再答。"
        fallback = True
    session.state = state
    db.commit()
    return {
        "action": "talk",
        "reply": text,
        "fallback": fallback,
        "session_key": session_key,
        "product": _product_brief(product),
        "trace": traces,
    }
