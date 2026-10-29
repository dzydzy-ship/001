# -*- coding: utf-8 -*-
"""促销利润防护：对话入口 + 直接测算 + 留痕查询。

路由前缀统一用 /agent，避免改动队友已有的 /products、/content 等路由文件。
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ProfitCheck, PromoConfig
from ..schemas import AgentChatRequest, ProfitCalcRequest
from ..services import agent, pricing

router = APIRouter(prefix="/agent", tags=["profit-agent"])


@router.post("/chat")
def chat(body: AgentChatRequest, db: Session = Depends(get_db)):
    """一轮对话：追问 / 出账 / 拒绝，三种结果之一。"""
    if not body.message.strip():
        raise HTTPException(400, "消息不能为空")
    return agent.handle(db, body.session_key, body.message, body.product_id)


@router.post("/reset")
def reset(body: dict, db: Session = Depends(get_db)):
    """清空某个会话的口径记忆（演示时用"重置"重新走一遍追问）。"""
    agent.reset_session(db, body.get("session_key", ""))
    return {"reset": True}


@router.get("/policy")
def get_policy():
    """把当前生效的口径与边界声明给前端展示——评委最爱问"你凭什么这么算"。"""
    data = agent.policy()
    return {
        "profit_floor_pct": data.get("profit_floor_pct"),
        "defaults": data.get("defaults", {}),
        "boundaries": data.get("boundaries", []),
        "clarify": data.get("clarify", {}),
    }


@router.get("/configs")
def list_configs(db: Session = Depends(get_db)):
    """返回商家确认过的口径配置（换平台/换店铺只改配置，不改代码）。"""
    rows = db.query(PromoConfig).all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "platform": row.platform,
            "commission_rate": row.commission_rate,
            "commission_base": row.commission_base,
            "ship_fee": row.ship_fee,
            "freight_dom": row.freight_dom,
            "freight_cover": row.freight_cover,
            "coupon_borne_by": row.coupon_borne_by,
            "tier_discounts": row.tier_discounts,
            "profit_floor_pct": row.profit_floor_pct,
            "is_default": row.is_default,
            "note": row.note,
        }
        for row in rows
    ]


@router.get("/product/{product_id}/guard")
def guard(product_id: int, qty: int = 2, tier: float = 0.8,
          coupon: float = 0.0, db: Session = Depends(get_db)):
    """内容页用：一行结论告诉你这款商品在当前促销下是否亏本。"""
    try:
        return agent.guard_product(db, product_id, qty=qty, tier_rate=tier, coupon=coupon)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/calc")
def calc(body: ProfitCalcRequest, db: Session = Depends(get_db)):
    """直接测算：不经过对话，给"改参数"面板用。"""
    inp = pricing.PricingInput(
        sku=body.sku,
        price=body.price,
        qty=body.qty,
        unit_cost=body.unit_cost,
        freight_intl=body.freight_intl,
        freight_dom=body.freight_dom,
        freight_cover=body.freight_cover,
        tier_rate=body.tier_rate,
        coupon=body.coupon,
        commission=body.commission,
        commission_base=body.commission_base,
        ship_fee=body.ship_fee,
    )
    try:
        result = pricing.run(inp)
        options = pricing.suggest_options(inp, body.floor_pct)
    except pricing.PricingError as exc:
        raise HTTPException(400, str(exc))
    return {"bill": result, "options": options,
            "ladder": agent.ladder(inp),
            "verdict": "risk" if result["利润率"] < body.floor_pct else "ok"}


@router.get("/checks")
def checks(limit: int = 20, db: Session = Depends(get_db)):
    """测算留痕：每次对话的入参与结果都可回看。"""
    rows = (db.query(ProfitCheck)
              .order_by(ProfitCheck.id.desc())
              .limit(max(1, min(limit, 100)))
              .all())
    return [
        {
            "id": row.id,
            "session_key": row.session_key,
            "sku": row.sku,
            "inputs": row.inputs,
            "result": row.result,
            "verdict": row.verdict,
            "explanation": row.explanation,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


@router.get("/store-scan")
def store_scan(qty: int = 2, tier: float = 0.8, coupon: float = 0.0,
               db: Session = Depends(get_db)) -> Dict[str, Any]:
    """全店体检：同一个活动铺到所有商品，逐款给结论并排序。"""
    return agent.store_scan(db, qty=qty, tier_rate=tier, coupon=coupon)
