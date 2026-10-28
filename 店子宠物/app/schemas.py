"""Pydantic 输入输出结构。AI 结构化输出也复用这些模型约束 JSON Schema。"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ProductCreate(BaseModel):
    """创建商品的入参。可只填 raw_material，让系统自动结构化提取。"""

    name: str = ""
    brand: str = ""
    category: str = ""
    specs: Dict[str, Any] = Field(default_factory=dict)
    price: Optional[float] = None
    cost_price: Optional[float] = None
    selling_points: List[str] = Field(default_factory=list)
    target_audience: Dict[str, Any] = Field(default_factory=dict)
    brand_tone: str = ""
    promotion: str = ""
    evidence: List[str] = Field(default_factory=list)
    restrictions: List[str] = Field(default_factory=list)
    tags: Dict[str, Any] = Field(default_factory=dict)
    raw_material: str = ""


class ProductExtract(BaseModel):
    """模块一步骤 2：结构化提取结果。缺失字段进入 missing_fields，不允许凭空补充。"""

    name: str = ""
    brand: str = ""
    category: str = ""
    specs: Dict[str, Any] = Field(default_factory=dict)
    price: Optional[float] = None
    cost_price: Optional[float] = None
    selling_points: List[str] = Field(default_factory=list)
    target_audience: Dict[str, Any] = Field(default_factory=dict)
    evidence: List[str] = Field(default_factory=list)
    restrictions: List[str] = Field(default_factory=list)
    tags: Dict[str, Any] = Field(default_factory=dict)
    missing_fields: List[str] = Field(default_factory=list)


class ExtractPreview(ProductExtract):
    """结构化提取预览（更聪明版）：在提取结果上叠加字段分类、置信度与待确认项。

    - confidence：每个字段的可信度 0~1（规则判定：原文显式 > 规则推断 > 模型推断）
    - field_sources：字段来源说明（原文显式 / 规则推断 / 模型推断 / 缺失）
    - price_roles：把资料里的价格拆成 成本 / 标价 / 批发 / 促销 四类
    - classified：字段 → 信息类别（事实类 / 规格类 / 营销类 / 价格类）
    - review：需要商家确认的点（多来源冲突、疑似营销话术当事实等）
    """

    confidence: Dict[str, float] = Field(default_factory=dict)
    field_sources: Dict[str, str] = Field(default_factory=dict)
    price_roles: Dict[str, Optional[float]] = Field(default_factory=dict)
    classified: Dict[str, str] = Field(default_factory=dict)
    review: List[str] = Field(default_factory=list)


class ProductDraft(BaseModel):
    """表格解析出的商品草稿（只预览、未入库）。"""

    name: str = ""
    sku: str = ""
    category: str = ""
    cost_price: Optional[float] = None
    price: Optional[float] = None
    wholesale_price: Optional[float] = None
    selling_points: List[str] = Field(default_factory=list)
    specs: Dict[str, Any] = Field(default_factory=dict)
    package_list: List[str] = Field(default_factory=list)
    evidence: List[str] = Field(default_factory=list)
    image_url: str = ""
    weight: str = ""
    raw_text: str = ""
    source_sheet: str = ""
    source_row: int = 0
    confidence: Dict[str, float] = Field(default_factory=dict)
    missing: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class ImportPreview(BaseModel):
    """表格解析预览（只读，不落库）。"""

    filename: str = ""
    ok: bool = True
    error: str = ""
    sheets: List[str] = Field(default_factory=list)
    table_type: str = ""
    header_row: int = 0
    columns: Dict[str, str] = Field(default_factory=dict)
    products: List[ProductDraft] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class ImportConfirmRequest(BaseModel):
    """确认导入：把预览里勾选/改过的草稿真正写进商品库。"""

    products: List[ProductDraft] = Field(default_factory=list)
    create_costs: bool = True


class BatchCalcRequest(BaseModel):
    """全店批量运算：按同一套口径/活动，给每个商品算推荐售价与活动后利润。"""

    margin: float = 20.0          # 期望毛利率（%）
    qty: int = 1                  # 满件数
    tier_rate: float = 1.0        # 折扣率（0.8 = 八折）
    coupon: float = 0.0           # 券额


class ContentRequest(BaseModel):
    product_id: int
    platform: str
    content_type: str = "post"


class ContentAnalyzeRequest(BaseModel):
    """生成前的产品分析请求（规则化提炼事实清单，不调大模型）。"""

    product_id: int
    platform: str = ""   # 可选：带上平台可返回该平台的切入角度


class ContentGenerate(BaseModel):
    """模块一步骤 4 的 AI 输出结构。"""

    title: str = ""
    body: str = ""
    tags: List[str] = Field(default_factory=list)
    cta: str = ""
    script: List[Dict[str, str]] = Field(default_factory=list)  # [{scene, text, note}]


class MatchJudge(BaseModel):
    """模块二第二阶段：大模型业务判断输出。"""

    relevance_reason: str = ""
    marketing_idea: str = ""
    content_form: str = ""
    p_score: int = Field(default=50, ge=0, le=100)  # 营销潜力
    c_score: int = Field(default=50, ge=0, le=100)  # 证据可信度
    risk: List[str] = Field(default_factory=list)
    checklist: List[str] = Field(default_factory=list)
    should_skip: bool = False


class MatchOpportunity(MatchJudge):
    """批量匹配：大模型从一批候选热点里挑出的单个机会。"""

    hotspot_index: int = Field(default=-1, ge=-1)  # 候选热点列表中的下标（从 0 开始）
    r_score: int = Field(default=50, ge=0, le=100)  # 关联强度（AI 判定，替代字面相似度）


class MatchBatch(BaseModel):
    """模块二：一次调用内让大模型从候选热点里挑出最值得跟进的机会。"""

    opportunities: List[MatchOpportunity] = Field(default_factory=list)


class MatchRequest(BaseModel):
    product_ids: Optional[List[int]] = None   # None = 全部商品
    hotspot_ids: Optional[List[int]] = None   # None = 全部非敏感热点
    hotspot_limit: int = Field(default=40, ge=1, le=200)  # 没指定 hotspot_ids 时，只匹配热度最高的 N 条
    judge_limit: int = Field(default=12, ge=1, le=100)    # 最多调用大模型精排的次数（控制耗时与成本）


class HotspotCreate(BaseModel):
    """新增热点（人工录入）。"""

    title: str
    description: str = ""
    source: str = "manual"
    source_url: str = ""
    category: str = ""
    keywords: List[str] = Field(default_factory=list)


class HotspotUpdate(BaseModel):
    """更新热点（仅更新显式传入字段）。"""

    title: Optional[str] = None
    description: Optional[str] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    category: Optional[str] = None
    keywords: Optional[List[str]] = None
    hot_score: Optional[float] = None
    trend: Optional[str] = None
    is_sensitive: Optional[bool] = None


# ---------------------------------------------------------------------------
# 促销利润防护（新增）
# ---------------------------------------------------------------------------


class ProfitCalcRequest(BaseModel):
    """直接调用测算引擎的入参。缺 price / unit_cost 时引擎会报错而非猜值。"""

    sku: str = ""
    product_id: Optional[int] = None
    price: Optional[float] = None
    qty: int = 1
    unit_cost: Optional[float] = None
    freight_intl: float = 0.0
    freight_dom: float = 19.9
    freight_cover: float = 0.0
    tier_rate: float = 1.0
    coupon: float = 0.0
    commission: float = 0.06
    commission_base: str = "gmv_after_coupon"
    ship_fee: float = 19.9
    floor_pct: float = 5.0


class AgentChatRequest(BaseModel):
    """对话入口：一句话进来，一整套账单出去。"""

    session_key: str
    message: str
    product_id: Optional[int] = None


class AgentPlan(BaseModel):
    """Agent 第一步：只把话拆成参数，不产生任何数字。"""

    intent: str = "price_check"      # price_check / write_action / chitchat
    product_hint: str = ""
    qty: Optional[int] = None
    tier_rate: Optional[float] = None
    coupon: Optional[float] = None
    price: Optional[float] = None
    unit_cost: Optional[float] = None
    freight_intl: Optional[float] = None
    freight_dom: Optional[float] = None
    ask: List[str] = Field(default_factory=list)   # 需要向商家追问的字段


class AgentNarration(BaseModel):
    """Agent 第四步：解释账单。只允许引用引擎字段里的数字。"""

    narrative: str = ""
    used_fields: List[str] = Field(default_factory=list)
    next_question: str = ""


class ProductCostUpdate(BaseModel):
    """商品成本档案：只有这几项可改，其余口径走全局。"""

    unit_cost: Optional[float] = None
    freight_intl: Optional[float] = None
    freight_dom: Optional[float] = None
    profit_floor_pct: Optional[float] = None
    note: str = ""


class LLMConfigRequest(BaseModel):
    """设置页填的模型接入信息（只在内存生效，重启清空）。"""

    api_key: str = ""
    base_url: str = ""
    model: str = ""


class PolicyUpdateRequest(BaseModel):
    """可编辑的计价口径。None 表示"这项不改"。"""
    default_coupon: Optional[float] = None

    name: Optional[str] = None
    commission_rate: Optional[float] = None
    commission_base: Optional[str] = None
    ship_fee: Optional[float] = None
    freight_dom: Optional[float] = None
    freight_cover: Optional[float] = None
    coupon_borne_by: Optional[str] = None
    tier_discounts: Optional[dict] = None
    profit_floor_pct: Optional[float] = None
