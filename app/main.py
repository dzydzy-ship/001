"""FastAPI 入口：装配数据库、路由、调度器与前端页面。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .config import FRONTEND_DIR
from .database import SessionLocal, init_db
from .routers import agent as agent_router
from .routers import content, costs, hotspots, matching, products, settings as settings_router
from .scheduler import start_scheduler, stop_scheduler
from .seed import seed_if_empty


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        seed_if_empty(db)
    finally:
        db.close()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="AI 商家营销运营助手", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

app.include_router(products.router)
app.include_router(content.router)
app.include_router(hotspots.router)
app.include_router(matching.router)
app.include_router(agent_router.router)
app.include_router(settings_router.router)
app.include_router(costs.router)


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}
