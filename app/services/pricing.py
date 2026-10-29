# -*- coding: utf-8 -*-
"""促销叠加利润测算引擎（纯函数，零外部依赖）。

设计原则（作品的硬约束）：

1. **每一个金额都由确定性公式算出**，大模型不得猜、也不允许参与运算；
2. 所有口径（谁承担优惠、按单还是按件、佣金基数）都是**显式入参**，
   缺参数就报错，不设"默认猜一个"的分支；
3. 输出是**逐项拆解**而不是一个利润数字——拆解才是 Agent 做归因的前提。

放在 services 层、且不 import 任何第三方库，是为了：
    * 可被 `tests/test_pricing.py` 单独跑（没有 fastapi/pydantic 也能测）；
    * 可被路由器、Agent、脚本、批量扫描四种方式复用。

字段（平台无关，换平台只换配置不换代码）：

    sku             商品标识（仅用于回显）
    price           标价（单件）
    qty             件数
    unit_cost       单件进货成本（出厂价 / 1688 档位价）
    freight_intl    跨国运输分摊（每件，元）
    freight_dom     国内运费（每单，元；卖家承担）
    freight_cover   平台包邮补贴覆盖的国内运费（每单，元）
    tier_rate       满件折扣率（两件八折 = 0.8；不满足条件传 1.0）
    coupon          优惠券（每单，元；仅计商家承担部分）
    commission      平台佣金率
    commission_base 佣金基数口径：'gmv_after_coupon'（券后成交额，平台把券算作降价，默认）
                    或 'gmv'（券前成交额，券由商家自担，平台仍按折后价抽佣）
    ship_fee        买家支付的运费（每单）。收付相抵不计成本，只进佣金基数
"""
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional


COMMISSION_BASES = ("gmv_after_coupon", "gmv")
# 历史值：老配置里存过 gmv_with_ship_fee（含运费），语义与券后口径一致——
# 因为 ship_fee 本来就已经加进佣金基数，所以直接并入，不再当第三档。
LEGACY_COMMISSION_BASES = {"gmv_with_ship_fee": "gmv_after_coupon"}


def normalize_commission_base(value: Optional[str]) -> str:
    """把历史值收敛成两档之一；认不出的原样返回，交给 validate 报错。"""
    raw = (value or "").strip()
    return LEGACY_COMMISSION_BASES.get(raw, raw)


class PricingError(ValueError):
    """入参不合法：宁可报错，也不静默按默认值算。"""


@dataclass(frozen=True)
class PricingInput:
    """一次测算的全部入参。字段缺失时用 None，由 validate 拦下来。"""

    sku: str = ""
    price: Optional[float] = None
    qty: int = 1
    unit_cost: Optional[float] = None
    freight_intl: float = 0.0
    freight_dom: float = 0.0
    freight_cover: float = 0.0
    tier_rate: float = 1.0
    coupon: float = 0.0
    commission: float = 0.0
    commission_base: str = "gmv_after_coupon"
    ship_fee: float = 19.9

    def as_dict(self) -> Dict:
        return asdict(self)


REQUIRED = ("price", "unit_cost")


def validate(inp: PricingInput) -> List[str]:
    """返回缺失/越界的字段说明；空列表表示可以算。"""
    problems: List[str] = []
    for field in REQUIRED:
        value = getattr(inp, field)
        if value is None:
            problems.append(f"缺少必填参数：{field}")
        elif value < 0:
            problems.append(f"参数越界：{field} 不能为负（当前 {value}）")

    if inp.qty is None or inp.qty < 1:
        problems.append("参数越界：qty 至少为 1")
    if not (0 < inp.tier_rate <= 1):
        problems.append("参数越界：tier_rate 应在 (0, 1] 之间（不打折传 1.0）")
    if not (0 <= inp.commission < 1):
        problems.append("参数越界：commission 应在 [0, 1) 之间")
    if normalize_commission_base(inp.commission_base) not in COMMISSION_BASES:
        problems.append("参数越界：commission_base 只能取 gmv_after_coupon（券后）或 gmv（券前）")
    if inp.coupon < 0:
        problems.append("参数越界：coupon 不能为负")
    return problems


def run(inp: PricingInput) -> Dict:
    """计算一次促销叠加的完整账单。返回逐项拆解，不是结论。"""
    problems = validate(inp)
    if problems:
        raise PricingError("；".join(problems))

    qty = int(inp.qty)
    gmv_listed = float(inp.price) * qty              # 标价总额
    gmv_after_tier = gmv_listed * inp.tier_rate      # 满件折扣后
    gmv = gmv_after_tier - inp.coupon                # 券后商品成交额（不含运费）

    goods_cost = float(inp.unit_cost) * qty
    intl_cost = inp.freight_intl * qty
    dom_cost = max(inp.freight_dom - inp.freight_cover, 0.0)

    # 买家支付的运费是实收收入，卖家发货运费是成本，两者不可混为一谈；
    # 佣金按"商品成交额 + 买家运费"计收（平台的计费口径）。
    revenue = gmv + inp.ship_fee
    base = normalize_commission_base(inp.commission_base)
    comm_base = gmv if base == "gmv_after_coupon" else gmv_after_tier
    commission = (comm_base + inp.ship_fee) * inp.commission

    profit = revenue - goods_cost - intl_cost - dom_cost - commission
    warnings: List[str] = []
    if gmv <= 0:
        warnings.append("券后成交额为 0 或负数：优惠额度已超过商品价格")
    if gmv - commission < goods_cost + intl_cost:
        warnings.append("扣掉平台佣金后的收入已盖不住进货成本：每卖一件都在倒贴")
    if inp.tier_rate < 1 and gmv_after_tier < goods_cost:
        warnings.append("仅满件折扣一项就已低于进货成本，券不是主因")

    return {
        "sku": inp.sku,
        "件数": qty,
        "标价总额": round(gmv_listed, 2),
        "折扣后金额": round(gmv_after_tier, 2),
        "成交额": round(gmv, 2),
        "订单实收": round(revenue, 2),
        "单件成交价": round(gmv / qty, 2) if qty else 0.0,
        "商品成本": round(goods_cost, 2),
        "跨国运费": round(intl_cost, 2),
        "国内运费": round(dom_cost, 2),
        "买家运费": round(inp.ship_fee, 2),
        "平台佣金": round(commission, 2),
        "佣金基数": round(comm_base + inp.ship_fee, 2),
        "利润": round(profit, 2),
        "利润率": round(profit / gmv * 100, 1) if gmv else 0.0,
        "每件利润": round(profit / qty, 2) if qty else 0.0,
        "警告": warnings,
        "入参": inp.as_dict(),
    }


# ---------------------------------------------------------------- 达标反解
def gmv_required(inp: PricingInput, margin: float) -> float:
    """要让「利润 / 商品成交额 = margin」，商品成交额至少要多少。

    利润 = (商品成交额 + 买家运费) * (1 - 佣金率) - 货物成本 - 运费
    令 利润 = margin * 商品成交额，解出商品成交额。
    """
    goods_cost = float(inp.unit_cost) * int(inp.qty)
    intl_cost = inp.freight_intl * int(inp.qty)
    dom_cost = max(inp.freight_dom - inp.freight_cover, 0.0)
    fixed = goods_cost + intl_cost + dom_cost
    denom = 1 - inp.commission - margin
    if denom <= 0:
        raise PricingError("目标利润率过高：佣金加利润率已超过 100%，无解")
    return (fixed - inp.ship_fee * (1 - inp.commission)) / denom


def suggest_options(inp: PricingInput, floor_pct: float) -> Dict:
    """在给定口径下，算出「原方案」与若干可选调整，并按利润排序。

    floor_pct 是商家的毛利率红线（单位：百分数，例如 5 表示 5%）。
    返回值里的 max_profitable_coupon 是"最多只能发多少券"的边界值。
    """
    margin = floor_pct / 100.0
    base = run(inp)

    need = gmv_required(inp, margin)
    gmv_after_tier = float(inp.price) * int(inp.qty) * inp.tier_rate
    max_coupon = round(gmv_after_tier - need, 2)

    options: List[Dict] = []

    def add(name: str, patch: Dict, note: str) -> None:
        try:
            result = run(_replace(inp, patch))
        except PricingError:
            return
        options.append({
            "名称": name,
            "说明": note,
            "利润": result["利润"],
            "利润率": result["利润率"],
            "成交额": result["成交额"],
            "改动": patch,
        })

    if inp.coupon > 0:
        add("撤掉优惠券", {"coupon": 0.0}, "只保留满件折扣，券全部撤回")
    if max_coupon > 0:
        add(f"券额降到 {max_coupon}", {"coupon": max_coupon},
            f"券后仍保住 {floor_pct}% 毛利率，这是能发的上限")
    elif inp.coupon > 0:
        add("券额降到 0", {"coupon": 0.0},
            f"按当前折扣结构，任何券都保不住 {floor_pct}% 毛利率")
    if inp.tier_rate < 1:
        add("只发券、不做满件折扣", {"tier_rate": 1.0, "qty": 1},
            "拆开两项优惠，逐项验证谁在吃掉利润")
    if inp.tier_rate < 1:
        solved = _solve_tier_rate(inp, margin)
        if solved is not None and solved < 1:
            add(f"满件折扣改为 {solved:.2f}", {"tier_rate": solved},
                f"退回折扣力度，保住 {floor_pct}% 毛利率")

    price_needed = _solve_price(inp, margin)
    if price_needed and price_needed > float(inp.price):
        add(f"标价提到 {price_needed:.2f}", {"price": price_needed},
            "不动优惠，用涨价把这条促销线拉回红线以上")

    options.sort(key=lambda item: item["利润"], reverse=True)
    return {
        "原方案": base,
        "毛利红线": floor_pct,
        "最多可发券": max_coupon,
        "达标所需成交额": round(need, 2),
        "可选调整": options,
        "是否跌破红线": base["利润率"] < floor_pct,
    }


def _replace(inp: PricingInput, patch: Dict) -> PricingInput:
    data = inp.as_dict()
    data.update(patch)
    return PricingInput(**data)


def _solve_tier_rate(inp: PricingInput, margin: float) -> Optional[float]:
    """反解满件折扣率：保持券额不变时的临界折扣。"""
    need = gmv_required(inp, margin)
    listed = float(inp.price) * int(inp.qty)
    if listed <= 0:
        return None
    rate = (need + inp.coupon) / listed
    return None if rate > 1.5 else round(rate, 2)


def _solve_price(inp: PricingInput, margin: float) -> Optional[float]:
    """反解单件标价：假设按单件购买（qty 不变）时的最低标价。"""
    if int(inp.qty) <= 0:
        return None
    need = gmv_required(inp, margin)
    per_unit_needed = (need + inp.coupon) / int(inp.qty) / inp.tier_rate
    return round(per_unit_needed, 2)


# ---------------------------------------------------------------- 推荐售价
def recommend_price(unit_cost: float, margin_pct: float,
                    commission: float = 0.0,
                    commission_base: str = "gmv_after_coupon",
                    ship_fee: float = 19.9,
                    freight_dom: float = 0.0,
                    freight_cover: float = 0.0,
                    freight_intl: float = 0.0,
                    qty: int = 1) -> Dict:
    """按「成本 + 运费 + 佣金 + 期望毛利率」反推单件推荐售价。

    直接复用 gmv_required / _solve_price：先解出「达标所需商品成交额」，
    再换算成单件标价。口径（佣金率、买家运费、国内运费）全部由调用方
    显式传入（来自设置页的计价口径），不在这里猜默认值；大模型只负责
    解释，不参与运算。与促销测算是同一套公式，保证「先定价 → 再测促销」一致。
    """
    if unit_cost is None or float(unit_cost) < 0:
        raise PricingError("缺少进货成本：先填成本再算推荐价")
    if not (0 <= float(margin_pct) < 100):
        raise PricingError("期望毛利率应在 0~100% 之间")
    if int(qty) < 1:
        raise PricingError("件数至少为 1")

    margin = float(margin_pct) / 100.0
    inp = PricingInput(
        sku="recommend",
        qty=int(qty),
        unit_cost=float(unit_cost),
        freight_intl=float(freight_intl),
        freight_dom=float(freight_dom),
        freight_cover=float(freight_cover),
        tier_rate=1.0,
        coupon=0.0,
        commission=float(commission),
        commission_base=commission_base,
        ship_fee=float(ship_fee),
    )

    breakeven_raw = _solve_price(inp, 0.0)
    target_raw = _solve_price(inp, margin)
    if breakeven_raw is None or target_raw is None:
        raise PricingError("件数必须至少为 1")
    breakeven = max(0.0, round(breakeven_raw, 2))
    target = max(breakeven, round(target_raw, 2))
    check = run(_replace(inp, {"price": target}))

    return {
        "提醒": "这是「没做活动」的定价参考，不是「做了活动还能赚」的价。"
                "要按活动算真实利润，请到「利润防护」用同一款商品选好折扣与券再测。",
        "推荐售价": target,
        "保本价": breakeven,
        "建议区间": [target, round(target * 1.2, 2)],
        "期望毛利率": round(float(margin_pct), 1),
        "实际毛利率": check["利润率"],
        "成本拆解": {
            "进货成本": round(float(unit_cost) * int(qty), 2),
            "跨国运费": round(float(freight_intl) * int(qty), 2),
            "国内运费": round(max(float(freight_dom) - float(freight_cover), 0.0), 2),
            "买家运费": round(float(ship_fee), 2),
            "平台佣金率": round(float(commission) * 100, 2),
        },
        "口径": {
            "佣金率": round(float(commission) * 100, 2),
            "佣金基数": commission_base,
            "买家运费": round(float(ship_fee), 2),
            "国内运费": round(float(freight_dom), 2),
            "跨国运费": round(float(freight_intl), 2),
            # 下面两个字段是"丑话说在前面"：这个价不含店内活动
            "含促销活动": False,
            "不含": "店内满件折扣、优惠券、退货率、广告费",
        },
        "测算校验": check,
    }


# ---------------------------------------------------------------- 出口定价
def export_price_by_margin(unit_cost_cny: float, margin: float,
                           intl_freight_cny: float = 550.0, rate: float = 6.7,
                           keep_rate: float = 0.94, shipping_usd: float = 19.9) -> Dict:
    """按目标利润率反解跨境售价（USD）。

    keep_rate = 1 - 平台佣金率；rate 为 USD→CNY 汇率。
    """
    fixed = unit_cost_cny + intl_freight_cny
    net = fixed / (1 - margin) if margin < 1 else float("inf")
    price_usd = net / (keep_rate * rate) - shipping_usd
    return {
        "利润率": margin,
        "售价USD": round(price_usd, 2),
        "售价折人民币": round(price_usd * rate, 2),
        "买家实付USD": round(price_usd + shipping_usd, 2),
        "净收入CNY": round(net, 2),
        "利润CNY": round(net - fixed, 2),
    }


# ---------------------------------------------------------------- 批量扫描
def scan(rows: List[Dict], floor_pct: float, defaults: Optional[Dict] = None) -> Dict:
    """批量体检一批 SKU：「哪些款在当前促销下不能打折」。

    rows 每项至少含 sku / price / unit_cost，其余字段用 defaults 补齐。
    """
    defaults = defaults or {}
    results: List[Dict] = []
    for row in rows:
        data = dict(defaults)
        data.update({k: v for k, v in row.items() if v is not None})
        inp = PricingInput(**{k: v for k, v in data.items() if k in PricingInput.__dataclass_fields__})
        try:
            result = run(inp)
        except PricingError as exc:
            results.append({"sku": row.get("sku", ""), "错误": str(exc)})
            continue
        results.append({
            "sku": inp.sku,
            "利润": result["利润"],
            "利润率": result["利润率"],
            "是否跌破红线": result["利润率"] < floor_pct,
        })
    results.sort(key=lambda item: item.get("利润率", 999))
    return {
        "毛利红线": floor_pct,
        "总数": len(results),
        "跌破数": sum(1 for r in results if r.get("是否跌破红线")),
        "明细": results,
    }
