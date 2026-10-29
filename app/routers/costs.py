"""商品的成本/运费档案接口（prefix 与队友的 /products 合并，不改他的文件）。"""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Product
from ..schemas import ProductCostUpdate
from ..services import costs as cost_service
from ..services import pricing

router = APIRouter(prefix="/products", tags=["product-cost"])


def _effective(db: Session, product: Product) -> Dict[str, Any]:
    """引擎实际会用到的三个数，以及它们各自的来源（可追溯）。"""
    override = cost_service.get_cost(db, product.id)
    config = None
    try:
        from ..services import agent as agent_service
        config = agent_service.default_config(db)
    except Exception:
        config = None
    unit_cost = override.get("unit_cost")
    source_uc = "商品档案" if unit_cost is not None else (
        "Product.cost_price" if getattr(product, "cost_price", None) is not None else "缺失")
    if unit_cost is None:
        unit_cost = getattr(product, "cost_price", None)
    return {
        "unit_cost": unit_cost,
        "freight_intl": override.get("freight_intl", 0.0),
        "freight_dom": override.get("freight_dom", getattr(config, "freight_dom", 19.9)),
        "sources": {
            "unit_cost": source_uc,
            "freight_intl": "商品档案" if "freight_intl" in override else "默认 0（跨国运费含在售价里）",
            "freight_dom": "商品档案" if "freight_dom" in override else "全局口径",
        },
    }


@router.get("/{product_id}/cost")
def get_product_cost(product_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="商品不存在")
    return {
        "product_id": product_id,
        "name": product.name,
        "price": product.price,
        "cost": cost_service.get_cost(db, product_id),
        "effective": _effective(db, product),
    }


@router.put("/{product_id}/cost")
def put_product_cost(product_id: int, body: ProductCostUpdate,
                     db: Session = Depends(get_db)) -> Dict[str, Any]:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="商品不存在")
    for label, value in (("进货成本", body.unit_cost), ("跨国运费", body.freight_intl),
                         ("国内运费", body.freight_dom)):
        if value is not None and value < 0:
            raise HTTPException(status_code=400, detail=f"{label}不能为负")
    payload = body.model_dump(exclude_unset=True)
    cost_service.upsert_cost(db, product_id, payload)
    return {
        "saved": True,
        "product_id": product_id,
        "cost": cost_service.get_cost(db, product_id),
        "effective": _effective(db, product),
    }


@router.get("/{product_id}/recommend-price")
def recommend_price(product_id: int, margin: float = 20.0,
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    """推荐售价：成本 + 设置里的运费/佣金 + 期望毛利率 → 反推单件标价。

    与促销测算共用同一个 pricing 引擎（gmv_required / _solve_price），
    保证「先定价 → 再测促销」两步用的是同一套公式，数字可逐项追溯。
    """
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="商品不存在")

    effective = _effective(db, product)
    unit_cost = effective.get("unit_cost")
    if unit_cost is None:
        raise HTTPException(
            status_code=400,
            detail="这款商品还没填进货成本，请先在「编辑商品」里填上成本再算推荐价",
        )

    from ..services import agent as agent_service
    kw = agent_service._config_kwargs(agent_service.default_config(db))

    try:
        result = pricing.recommend_price(
            unit_cost=float(unit_cost),
            margin_pct=float(margin),
            commission=float(kw["commission"]),
            commission_base=kw["commission_base"],
            ship_fee=float(kw["ship_fee"]),
            freight_dom=float(effective["freight_dom"]),
            freight_cover=float(kw["freight_cover"]),
            freight_intl=float(effective["freight_intl"] or 0.0),
        )
    except pricing.PricingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    result["product_id"] = product_id
    result["product_name"] = product.name
    result["current_price"] = product.price
    return result
