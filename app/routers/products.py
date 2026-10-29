"""商品管理：创建（可自动结构化提取）、查询、预览提取、编辑、删除、表格批量导入、全店批量运算。"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Product, ProductCost
from ..schemas import (BatchCalcRequest, ImportConfirmRequest, ImportPreview,
                       ProductCreate, ProductExtract)
from ..services import agent as agent_service
from ..services import pricing
from ..services.extraction import extract_product
from ..services.table_parser import parse_bytes

router = APIRouter(prefix="/products", tags=["products"])

MAX_UPLOAD_BYTES = 30 * 1024 * 1024  # 单文件上限 30MB


@router.post("/import/preview", response_model=ImportPreview)
async def import_preview(file: UploadFile = File(...)):
    """上传 Excel（.xls/.xlsx）→ 通用解析 → 返回商品草稿预览，不写库。"""
    data = await file.read()
    if not data:
        raise HTTPException(400, "文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "文件超过 30MB，请拆表后再上传")
    preview = parse_bytes(data, file.filename or "upload.xlsx")
    if not preview.ok:
        raise HTTPException(400, preview.error or "表格解析失败")
    return preview


@router.post("/import/confirm")
def import_confirm(body: ImportConfirmRequest, db: Session = Depends(get_db)):
    """确认导入：把预览里勾选/改过的草稿写入商品库（同一条目按名称去重）。"""
    existing = {p.name.strip() for p in db.query(Product).all() if p.name}
    created, skipped, duplicated = [], 0, 0
    for draft in body.products:
        name = (draft.name or draft.sku or "").strip()
        if not name:
            skipped += 1
            continue
        if name in existing:
            duplicated += 1
            continue
        product = Product(
            name=name,
            brand="",
            category=draft.category,
            specs=draft.specs,
            price=draft.price,
            cost_price=draft.cost_price,
            selling_points=draft.selling_points,
            target_audience={},
            evidence=draft.evidence,
            restrictions=[],
            tags={},
            raw_material=draft.raw_text,
            missing_fields=draft.missing,
        )
        db.add(product)
        db.flush()
        existing.add(name)
        if body.create_costs and (draft.cost_price is not None):
            db.add(ProductCost(product_id=product.id, unit_cost=draft.cost_price, note=f"表格导入：{draft.source_sheet}"))
        created.append(product)
    db.commit()
    for p in created:
        db.refresh(p)
    return {"created": len(created), "skipped": skipped, "duplicated": duplicated, "ids": [p.id for p in created]}


@router.post("/extract", response_model=None)
def preview_extract(body: dict):
    """预览：不落库，仅对 raw_material 做结构化提取（含字段分类/置信度/待确认）。"""
    return extract_product(body.get("raw_material", ""))


@router.post("/batch-calc")
def batch_calc(body: BatchCalcRequest, db: Session = Depends(get_db)):
    """全店批量运算：按同一套口径算每款推荐售价/保本价，并按给定活动算利润与风险。"""
    kw = agent_service._config_kwargs(agent_service.default_config(db))
    cost_rows = {c.product_id: c for c in db.query(ProductCost).all()}
    has_activity = bool(body.tier_rate < 1 or body.coupon > 0)
    rows = []
    ok_count = missing = 0

    for p in db.query(Product).order_by(Product.id).all():
        cost_row = cost_rows.get(p.id)
        unit_cost = p.cost_price if p.cost_price is not None else (cost_row.unit_cost if cost_row else None)
        base = {"product_id": p.id, "name": p.name, "price": p.price, "cost": unit_cost}
        if unit_cost is None or float(unit_cost) <= 0:
            missing += 1
            rows.append({**base, "status": "missing_cost", "message": "缺进货成本：先填成本再算",
                         "breakeven": None, "recommend": None, "band": [], "activity": None})
            continue

        try:
            rec = pricing.recommend_price(
                unit_cost=float(unit_cost), margin_pct=body.margin,
                commission=kw.get("commission", 0.0),
                commission_base=kw.get("commission_base", "gmv_after_coupon"),
                ship_fee=kw.get("ship_fee", 19.9),
                freight_dom=(cost_row.freight_dom if cost_row else 0.0) or 0.0,
                freight_cover=kw.get("freight_cover", 0.0) or 0.0,
                freight_intl=(cost_row.freight_intl if cost_row else 0.0) or 0.0,
            )
        except pricing.PricingError as exc:
            missing += 1
            rows.append({**base, "status": "error", "message": str(exc),
                         "breakeven": None, "recommend": None, "band": [], "activity": None})
            continue

        row = {**base, "status": "ok", "message": "", "breakeven": rec["保本价"],
               "recommend": rec["推荐售价"], "band": rec["建议区间"],
               "target_margin": rec["期望毛利率"], "activity": None}
        price_used = float(p.price) if p.price else float(rec["推荐售价"])
        if has_activity:
            try:
                bill = pricing.run(pricing.PricingInput(
                    sku=str(p.id), price=price_used, qty=int(body.qty),
                    unit_cost=float(unit_cost),
                    freight_intl=(cost_row.freight_intl if cost_row else 0.0) or 0.0,
                    freight_dom=(cost_row.freight_dom if cost_row else 0.0) or 0.0,
                    freight_cover=kw.get("freight_cover", 0.0) or 0.0,
                    tier_rate=float(body.tier_rate), coupon=float(body.coupon),
                    commission=kw.get("commission", 0.0),
                    commission_base=kw.get("commission_base", "gmv_after_coupon"),
                    ship_fee=kw.get("ship_fee", 19.9),
                ))
                row["activity"] = {"price_used": round(price_used, 2), "profit": bill["利润"],
                                   "margin": bill["利润率"], "per_unit": bill["每件利润"],
                                   "warnings": bill["警告"]}
            except pricing.PricingError as exc:
                row["activity"] = {"price_used": round(price_used, 2), "profit": None,
                                   "margin": None, "per_unit": None, "warnings": [str(exc)]}
        ok_count += 1
        rows.append(row)

    return {
        "rows": rows,
        "summary": {"total": len(rows), "ok": ok_count, "missing_cost": missing,
                    "has_activity": has_activity},
        "policy": {"margin": body.margin, "qty": body.qty, "tier_rate": body.tier_rate,
                   "coupon": body.coupon, "commission": kw.get("commission", 0.0),
                   "ship_fee": kw.get("ship_fee", 19.9)},
    }



@router.post("", status_code=201)
def create_product(body: ProductCreate, db: Session = Depends(get_db)):
    """创建商品。若提供 raw_material 且结构化字段为空，则自动提取填充。"""
    data = body.model_dump()
    raw = data.pop("raw_material", "")
    extracted: ProductExtract | None = None

    # 若结构化字段为空但给了原始资料，先提取
    if raw and not any([data["name"], data["specs"], data["selling_points"]]):
        extracted = extract_product(raw)
        for field in ("name", "brand", "category", "specs", "price", "cost_price",
                      "selling_points", "target_audience", "evidence", "restrictions"):
            if getattr(extracted, field):
                data[field] = getattr(extracted, field)

    data["raw_material"] = raw
    data["missing_fields"] = extracted.missing_fields if extracted else []

    product = Product(**data)
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@router.get("")
def list_products(db: Session = Depends(get_db)):
    return db.query(Product).order_by(Product.id.desc()).all()


@router.get("/{product_id}")
def get_product(product_id: int, db: Session = Depends(get_db)):
    p = db.get(Product, product_id)
    if not p:
        raise HTTPException(404, "商品不存在")
    return p


@router.put("/{product_id}")
def update_product(product_id: int, body: ProductCreate, db: Session = Depends(get_db)):
    """更新商品信息；若提供新的 raw_material 则重新结构化提取覆盖。"""
    p = db.get(Product, product_id)
    if not p:
        raise HTTPException(404, "商品不存在")

    data = body.model_dump()
    raw = data.pop("raw_material", "")
    for field, value in data.items():
        setattr(p, field, value)

    if raw:
        p.raw_material = raw
        extracted = extract_product(raw)
        for field in ("name", "brand", "category", "specs", "price", "cost_price",
                      "selling_points", "target_audience", "evidence", "restrictions"):
            if getattr(extracted, field):
                setattr(p, field, getattr(extracted, field))
        p.missing_fields = extracted.missing_fields

    db.commit()
    db.refresh(p)
    return p


@router.delete("/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    p = db.get(Product, product_id)
    if not p:
        raise HTTPException(404, "商品不存在")
    db.delete(p)
    db.commit()
    return {"deleted": True, "id": product_id}
