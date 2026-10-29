"""模块一：内容生成与审核。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Content, Product
from ..schemas import ContentAnalyzeRequest, ContentRequest
from ..services.audit import audit_content
from ..services.content import (
    analyze_product,
    generate_content,
    get_platform_config,
    list_platforms,
)

router = APIRouter(prefix="/content", tags=["content"])


@router.get("/platforms")
def platforms():
    return list_platforms()


@router.post("/analyze")
def analyze(body: ContentAnalyzeRequest, db: Session = Depends(get_db)):
    """生成前产品分析：提炼事实清单、卖点、人群、场景、痛点、缺口与平台切入角度。"""
    p = db.get(Product, body.product_id)
    if not p:
        raise HTTPException(404, "商品不存在")
    brief = analyze_product(p)
    if body.platform:
        brief["current_angle"] = (brief.get("angles") or {}).get(body.platform, "")
    return brief


@router.post("/generate", status_code=201)
def generate(body: ContentRequest, db: Session = Depends(get_db)):
    p = db.get(Product, body.product_id)
    if not p:
        raise HTTPException(404, "商品不存在")

    result, brief, fact = generate_content(p, body.platform, body.content_type)
    audit = audit_content(result, p, get_platform_config(body.platform))

    # 把事实校验结果合并进 audit_result，前端统一展示
    notes = list(audit.get("notes") or [])
    for it in fact.get("issues") or []:
        tag = "【事实】" if it.get("level") == "high" else "【提示】"
        notes.append(tag + str(it.get("msg", "")))
    if not fact.get("issues"):
        notes.append("事实校验通过：未发现资料外数据或无依据宣称")
    audit["passed"] = bool(audit.get("passed")) and bool(fact.get("passed"))
    audit["notes"] = notes
    audit["fact_check"] = fact
    audit["brief"] = brief

    content = Content(
        product_id=p.id,
        platform=body.platform,
        content_type=body.content_type,
        title=result.title,
        body=result.body,
        tags=result.tags,
        cta=result.cta,
        script=result.script,
        audit_result=audit,
        status="pending",
    )
    db.add(content)
    db.commit()
    db.refresh(content)
    return content


@router.get("")
def list_content(db: Session = Depends(get_db)):
    return db.query(Content).order_by(Content.id.desc()).all()


@router.get("/{content_id}")
def get_content(content_id: int, db: Session = Depends(get_db)):
    c = db.get(Content, content_id)
    if not c:
        raise HTTPException(404, "内容不存在")
    return c


@router.post("/{content_id}/review")
def review(content_id: int, body: dict, db: Session = Depends(get_db)):
    c = db.get(Content, content_id)
    if not c:
        raise HTTPException(404, "内容不存在")
    status = body.get("status")
    if status not in ("approved", "rejected"):
        raise HTTPException(400, "status 必须为 approved 或 rejected")
    c.status = status
    db.commit()
    db.refresh(c)
    return c


@router.delete("/{content_id}")
def delete_content(content_id: int, db: Session = Depends(get_db)):
    c = db.get(Content, content_id)
    if not c:
        raise HTTPException(404, "内容不存在")
    db.delete(c)
    db.commit()
    return {"deleted": True, "id": content_id}
