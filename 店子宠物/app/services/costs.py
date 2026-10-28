"""商品的成本与运费档案（一品一档）。

为什么单开一张表：`Base.metadata.create_all()` 只建新表，不会给已有的
`products` 加列——老库直接跑会报 "no such column"。所以成本细节放这里，
纯增量，不动队友的任何表。
"""
from typing import Any, Dict, Optional

from ..models import ProductCost

FIELDS = ("unit_cost", "freight_intl", "freight_dom", "profit_floor_pct", "note")


def get_cost(db, product_id: Optional[int]) -> Dict[str, Any]:
    """取单个商品的成本覆盖；没有就返回空 dict（调用方自然回落到全局口径）。"""
    if db is None or not product_id:
        return {}
    row = db.query(ProductCost).filter(ProductCost.product_id == product_id).first()
    if row is None:
        return {}
    return {k: getattr(row, k) for k in FIELDS if getattr(row, k) is not None}


def upsert_cost(db, product_id: int, payload: Dict[str, Any]) -> ProductCost:
    """新增或更新；只写合法字段，None 表示"这条没填"。"""
    row = db.query(ProductCost).filter(ProductCost.product_id == product_id).first()
    if row is None:
        row = ProductCost(product_id=product_id)
        db.add(row)
    for key in FIELDS:
        if key in payload:
            setattr(row, key, payload[key])
    db.commit()
    db.refresh(row)
    return row


def as_dict(row: Optional[ProductCost]) -> Dict[str, Any]:
    if row is None:
        return {}
    data = {"product_id": row.product_id}
    for key in FIELDS:
        data[key] = getattr(row, key)
    data["updated_at"] = row.updated_at.isoformat() if row.updated_at else None
    return data
