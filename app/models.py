"""ORM 数据模型：商品、热点、生成内容、热点匹配机会卡。"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, JSON, String, Text

from .database import Base


class Product(Base):
    """结构化后的商品信息（原始资料提取一次后长期复用）。"""

    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    brand = Column(String(100), default="")
    category = Column(String(100), default="")
    specs = Column(JSON, default=dict)          # {容量, 材质, 尺寸, ...}
    price = Column(Float, nullable=True)
    cost_price = Column(Float, nullable=True)
    selling_points = Column(JSON, default=list)  # 核心卖点
    target_audience = Column(JSON, default=dict)  # {年龄段, 场景, 需求, 顾虑}
    brand_tone = Column(String(200), default="")
    promotion = Column(String(500), default="")
    evidence = Column(JSON, default=list)        # 检测报告、认证等
    restrictions = Column(JSON, default=list)    # 禁用表述、合规限制
    missing_fields = Column(JSON, default=list)  # 提取时标记的待确认字段
    tags = Column(JSON, default=dict)            # AI 自动标签：{品类,功能,场景,人群,痛点,价格带,可蹭热点类型}
    raw_material = Column(Text, default="")      # 商家原始资料
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Hotspot(Base):
    """热点及其热度时间序列。"""

    __tablename__ = "hotspots"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(300), nullable=False)
    description = Column(Text, default="")
    source = Column(String(100), default="")
    source_url = Column(String(500), default="")
    category = Column(String(100), default="")
    hot_score = Column(Float, default=0.0)
    trend = Column(String(20), default="stable")  # rising / peak / declining / stable
    keywords = Column(JSON, default=list)
    heat_history = Column(JSON, default=list)     # [{ts, score}, ...] 热度快照
    is_sensitive = Column(Boolean, default=False)
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Content(Base):
    """模块一生成的平台宣传内容。"""

    __tablename__ = "contents"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, nullable=False)
    platform = Column(String(50), nullable=False)
    content_type = Column(String(50), default="post")  # post / script / detail
    title = Column(String(500), default="")
    body = Column(Text, default="")
    tags = Column(JSON, default=list)
    cta = Column(String(500), default="")
    script = Column(JSON, default=list)   # 短视频分镜 [{scene, text, note}]
    audit_result = Column(JSON, default=dict)  # {passed, notes}
    status = Column(String(20), default="draft")  # draft / pending / approved / rejected
    created_at = Column(DateTime, default=datetime.utcnow)


class Match(Base):
    """模块二输出的热点营销机会卡。"""

    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, index=True)
    hotspot_id = Column(Integer, nullable=False)
    product_id = Column(Integer, nullable=False)
    score = Column(Float, default=0.0)          # S = 0.30R+0.25T+0.20A+0.15P+0.10C
    subscores = Column(JSON, default=dict)       # {r, t, a, p, c}
    relevance_reason = Column(Text, default="")  # 关联逻辑
    marketing_idea = Column(Text, default="")    # 营销创意
    content_form = Column(String(200), default="")
    risk = Column(JSON, default=list)
    checklist = Column(JSON, default=list)       # 发布前检查
    status = Column(String(20), default="pending")  # pending / approved / rejected
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# 促销利润防护（新增模块）：只新增表，不改动上面任何一张表的字段。
# ---------------------------------------------------------------------------


class PromoConfig(Base):
    """一套"商家确认过的口径"。

    引擎是平台无关的，换平台只换一条 PromoConfig，不改代码。
    """

    __tablename__ = "promo_configs"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    platform = Column(String(50), default="taobao")
    commission_rate = Column(Float, default=0.06)
    commission_base = Column(String(30), default="gmv_after_coupon")
    ship_fee = Column(Float, default=19.9)
    freight_dom = Column(Float, default=19.9)
    freight_cover = Column(Float, default=0.0)
    coupon_borne_by = Column(String(20), default="merchant")
    tier_discounts = Column(JSON, default=dict)       # {"2": 0.8, "3": 0.75}
    profit_floor_pct = Column(Float, default=5.0)
    # 促销体检默认券面额（元）：内容页那条提示条与全店体检默认值都用它，改设置即生效
    default_coupon = Column(Float, default=29.0)     # 毛利率红线
    is_default = Column(Boolean, default=False)
    note = Column(String(300), default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class ProfitCheck(Base):
    """一次测算的留痕：入参快照 + 结果拆解 + 归因文本。

    留痕的意义：评委问"这个数字怎么来的"，可以点开这一行逐项核对。
    """

    __tablename__ = "profit_checks"

    id = Column(Integer, primary_key=True, index=True)
    session_key = Column(String(64), default="")
    product_id = Column(Integer, nullable=True)
    sku = Column(String(200), default="")
    inputs = Column(JSON, default=dict)      # 引擎入参快照
    result = Column(JSON, default=dict)      # 引擎输出（逐项拆解）
    options = Column(JSON, default=list)     # 可选调整
    verdict = Column(String(20), default="ok")  # ok / risk
    explanation = Column(Text, default="")   # Agent 给出的归因解释
    created_at = Column(DateTime, default=datetime.utcnow)


class AgentSession(Base):
    """多轮对话的上下文：参数逐步补齐的过程本身就是演示素材。"""

    __tablename__ = "agent_sessions"

    id = Column(Integer, primary_key=True, index=True)
    session_key = Column(String(64), index=True, nullable=False)
    product_id = Column(Integer, nullable=True)
    state = Column(JSON, default=dict)       # {qty, tier_rate, coupon, missing, ...}
    turns = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProductCost(Base):
    """单个商品的成本与运费档案（一品一档，覆盖全局口径里的默认值）。

    为什么要单独一张表：`create_all()` 只建新表、不会给已有的 Product 加列，
    所以成本细节放在这里，老库不用迁移也不会报 "no such column"。
    取值优先级：本轮对话/表单显式传入 > 本表 > 全局 PromoConfig。
    """

    __tablename__ = "product_costs"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, index=True, nullable=False, unique=True)
    unit_cost = Column(Float, nullable=True)         # 进货成本（出厂价）
    freight_intl = Column(Float, default=0.0)        # 跨国运费 / 件
    freight_dom = Column(Float, default=19.9)        # 国内发货运费 / 单
    profit_floor_pct = Column(Float, nullable=True)  # 这款自己的毛利率红线（空=用全局）
    note = Column(String(300), default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
