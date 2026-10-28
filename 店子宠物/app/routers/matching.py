"""模块二：热点与商品匹配，输出机会卡。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Match
from ..schemas import MatchRequest
from ..services.matching import run_matching

router = APIRouter(prefix="/matching", tags=["matching"])


@router.post("/run", status_code=201)
def run(body: MatchRequest, db: Session = Depends(get_db)):
    created = run_matching(
        db,
        body.product_ids,
        body.hotspot_ids,
        hotspot_limit=body.hotspot_limit,
        judge_limit=body.judge_limit,
    )
    return [m for m in created]


@router.get("")
def list_matches(db: Session = Depends(get_db)):
    return db.query(Match).order_by(Match.score.desc()).all()


@router.get("/{match_id}")
def get_match(match_id: int, db: Session = Depends(get_db)):
    m = db.get(Match, match_id)
    if not m:
        raise HTTPException(404, "机会卡不存在")
    return m


@router.post("/{match_id}/review")
def review(match_id: int, body: dict, db: Session = Depends(get_db)):
    m = db.get(Match, match_id)
    if not m:
        raise HTTPException(404, "机会卡不存在")
    status = body.get("status")
    if status not in ("approved", "rejected"):
        raise HTTPException(400, "status 必须为 approved 或 rejected")
    m.status = status
    db.commit()
    db.refresh(m)
    return m
