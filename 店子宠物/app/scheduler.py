"""热点定时采集。MVP 用 APScheduler 后台任务，规模扩大后可换 Celery Beat。"""
from apscheduler.schedulers.background import BackgroundScheduler

from .config import settings
from .database import SessionLocal
from .services.hotspots import collect_hotspots

scheduler = BackgroundScheduler()


def _job() -> None:
    db = SessionLocal()
    try:
        collect_hotspots(db)
    finally:
        db.close()


def start_scheduler() -> None:
    if scheduler.running:
        return
    scheduler.add_job(
        _job, "interval", minutes=settings.hotspot_interval_minutes, id="collect_hotspots"
    )
    scheduler.start()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
