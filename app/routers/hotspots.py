"""模块二：热点采集、展示与人工维护。"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Hotspot
from ..schemas import HotspotCreate, HotspotUpdate
from ..services.hotspots import collect_hotspots

router = APIRouter(prefix="/hotspots", tags=["hotspots"])


@router.post("/refresh")
def refresh(db: Session = Depends(get_db)):
    """手动触发一次热点采集，返回新增/更新明细。"""
    return collect_hotspots(db)


@router.post("", status_code=201)
def create_hotspot(body: HotspotCreate, db: Session = Depends(get_db)):
    """人工新增热点。"""
    now = datetime.now(timezone.utc)
    h = Hotspot(
        title=body.title,
        description=body.description,
        source=body.source or "manual",
        source_url=body.source_url,
        category=body.category,
        keywords=body.keywords,
        hot_score=50.0,
        trend="stable",
        heat_history=[{"ts": now.isoformat(), "score": 50.0}],
        is_sensitive=False,
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


@router.put("/{hotspot_id}")
def update_hotspot(hotspot_id: int, body: HotspotUpdate, db: Session = Depends(get_db)):
    h = db.get(Hotspot, hotspot_id)
    if not h:
        raise HTTPException(404, "热点不存在")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(h, field, value)
    db.commit()
    db.refresh(h)
    return h


@router.delete("/{hotspot_id}")
def delete_hotspot(hotspot_id: int, db: Session = Depends(get_db)):
    h = db.get(Hotspot, hotspot_id)
    if not h:
        raise HTTPException(404, "热点不存在")
    db.delete(h)
    db.commit()
    return {"deleted": True, "id": hotspot_id}


@router.get("")
def list_hotspots(db: Session = Depends(get_db)):
    return db.query(Hotspot).order_by(Hotspot.hot_score.desc()).all()


@router.get("/{hotspot_id}")
def get_hotspot(hotspot_id: int, db: Session = Depends(get_db)):
    h = db.get(Hotspot, hotspot_id)
    if not h:
        raise HTTPException(404, "热点不存在")
    return h
