"""设置接口：大模型接入 + 计价口径。

两条设计约束：
1. Key 只在后端使用，任何响应都不回完整 Key（只回 `sk-abc…4688` 掩码）。
2. Key 只存内存（用户选择"每次重填"），服务重启即清空，不落库、不写日志。
"""
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..llm import llm
from ..models import PromoConfig
from ..schemas import LLMConfigRequest, PolicyUpdateRequest
from ..services import agent as agent_service

router = APIRouter(prefix="/settings", tags=["settings"])

COMMISSION_BASES = ("gmv_after_coupon", "gmv")
# 历史值 gmv_with_ship_fee 仍在库里存过，读的时候按券后口径处理
_LEGACY_COMMISSION_BASES = {"gmv_with_ship_fee": "gmv_after_coupon"}

# 只保留这三家，其余一律不支持：少一个选择，少一处配错。
# base_url 与默认模型由后端给出，前端切换服务商时自动填；Key 永远由用户自己填。
LLM_PROVIDERS = (
    {
        "key": "deepseek",
        "label": "DeepSeek（深度求索）",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "note": "国内直连、价格低。模型可改 deepseek-chat（通用）或 deepseek-reasoner（推理）。",
    },
    {
        "key": "openai",
        "label": "ChatGPT（OpenAI）",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "note": "国内网络一般需要代理才能连通。模型可改 gpt-4o-mini 或 gpt-4o。",
    },
    {
        "key": "qwen",
        "label": "通义千问（阿里云百炼）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "note": "在阿里云百炼控制台申请 Key，走 OpenAI 兼容模式。模型可改 qwen-plus 或 qwen-max。",
    },
)
DEFAULT_PROVIDER = "deepseek"


def match_provider(base_url: str = "") -> str:
    """从接口地址反推服务商；认不出来就返回空串（允许用户填自定义代理）。"""
    url = (base_url or "").strip().lower().rstrip("/")
    if not url:
        return ""
    for item in LLM_PROVIDERS:
        if url == item["base_url"].lower().rstrip("/") or url.startswith(item["base_url"].lower().rstrip("/")):
            return item["key"]
    if "dashscope" in url:
        return "qwen"
    if "openai" in url:
        return "openai"
    if "deepseek" in url:
        return "deepseek"
    return ""


# ---------------- 大模型接入 ----------------

@router.get("/llm/providers")
def get_llm_providers() -> Dict[str, Any]:
    """只有这三家。前端拿它渲染下拉框，切换时自动填地址与默认模型。"""
    return {
        "providers": list(LLM_PROVIDERS),
        "default": DEFAULT_PROVIDER,
        "note": "Key 由你自己填；地址与默认模型切换服务商时自动带出，一般不用改。",
    }


@router.get("/llm")
def get_llm() -> Dict[str, Any]:
    data = llm.describe()
    data["provider"] = match_provider(getattr(llm, "base_url", "") or "")
    data["note"] = "Key 只存在本次运行的内存里，重启服务即清空；不会写库、不写日志。"
    return data


@router.post("/llm")
def set_llm(body: LLMConfigRequest) -> Dict[str, Any]:
    if not body.api_key.strip():
        raise HTTPException(status_code=400, detail="API Key 不能为空")
    llm.reconfigure(api_key=body.api_key, base_url=body.base_url, model=body.model)
    data = llm.describe()
    data["saved"] = True
    return data


@router.post("/llm/test")
def test_llm(body: LLMConfigRequest) -> Dict[str, Any]:
    """先用这份配置发一个最小请求；成功再保存，避免存进去发现不通。"""
    if not body.api_key.strip():
        return {"ok": False, "error": "API Key 为空"}
    saved = (llm.api_key, llm.base_url, llm.model)
    llm.reconfigure(api_key=body.api_key, base_url=body.base_url, model=body.model)
    result = llm.ping()
    if not result.get("ok"):
        llm.api_key, llm.base_url, llm.model = saved  # 测不通就回滚，别把可用配置弄坏
    return result


@router.delete("/llm")
def clear_llm() -> Dict[str, Any]:
    llm.clear()
    data = llm.describe()
    data["cleared"] = True
    return data


# ---------------- 计价口径 ----------------

def _ensure_default(db: Session) -> PromoConfig:
    """默认口径若还没落库，就把 YAML 默认写成库里的一条记录。"""
    row = db.query(PromoConfig).filter(PromoConfig.is_default.is_(True)).first()
    if row is not None:
        return row
    yaml_default = agent_service.default_config(db)  # 可能只是内存对象
    row = PromoConfig(
        name=getattr(yaml_default, "name", "默认口径") or "默认口径",
        platform=getattr(yaml_default, "platform", "taobao") or "taobao",
        commission_rate=getattr(yaml_default, "commission_rate", 0.06) or 0.06,
        commission_base=getattr(yaml_default, "commission_base", "gmv_after_coupon"),
        ship_fee=getattr(yaml_default, "ship_fee", 19.9),
        freight_dom=getattr(yaml_default, "freight_dom", 19.9),
        freight_cover=getattr(yaml_default, "freight_cover", 0.0),
        coupon_borne_by=getattr(yaml_default, "coupon_borne_by", "merchant"),
        tier_discounts=getattr(yaml_default, "tier_discounts", {"2": 0.8}) or {"2": 0.8},
        profit_floor_pct=getattr(yaml_default, "profit_floor_pct", 5.0),
        default_coupon=getattr(yaml_default, "default_coupon", 29.0) or 29.0,
        is_default=True,
        note="默认口径",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _policy_payload(row: PromoConfig) -> Dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "platform": row.platform,
        "commission_rate": row.commission_rate,
        "commission_base": row.commission_base,
        "ship_fee": row.ship_fee,
        "freight_dom": row.freight_dom,
        "freight_cover": row.freight_cover,
        "coupon_borne_by": row.coupon_borne_by,
        "tier_discounts": row.tier_discounts or {},
        "profit_floor_pct": row.profit_floor_pct,
        "default_coupon": 29.0 if row.default_coupon is None else row.default_coupon,
        "updated_at": row.created_at.isoformat() if row.created_at else None,
    }


def _promo_defaults(row: PromoConfig) -> Dict[str, Any]:
    """促销体检 / 全店体检用的默认活动：满几件、打几折、券多少。

    这三项全部来自当前生效口径，所以设置里改完折扣或券额，体检结果立即跟着变。
    """
    discounts = row.tier_discounts or {}
    return {
        "qty": 2,
        "tier": float(discounts.get("2", 0.8)),
        "coupon": 29.0 if row.default_coupon is None else float(row.default_coupon),
    }


@router.get("/policy")
def get_policy(db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = _ensure_default(db)
    yaml_policy = agent_service.policy()
    return {
        "config": _policy_payload(row),
        "promo_defaults": _promo_defaults(row),
        "yaml_default_policy": yaml_policy.get("profit_floor_pct", 5.0),
        "commission_bases": list(COMMISSION_BASES),
        "boundaries": (yaml_policy.get("boundaries") or []),
        "note": "改完立即生效；已留痕的测算记录保留当时的入参快照，不会被改写。",
    }


def _validate(body: PolicyUpdateRequest) -> List[str]:
    errors: List[str] = []
    if body.commission_rate is not None and not (0 <= body.commission_rate <= 0.3):
        errors.append("平台佣金率应在 0%~30% 之间")
    if body.profit_floor_pct is not None and not (-50 <= body.profit_floor_pct <= 50):
        errors.append("毛利率红线应在 -50%~50% 之间")
    for label, value in (("买家运费", body.ship_fee), ("国内运费", body.freight_dom),
                         ("平台补贴", body.freight_cover)):
        if value is not None and value < 0:
            errors.append(f"{label}不能为负")
    if body.default_coupon is not None and not (0 <= body.default_coupon <= 5000):
        errors.append("默认券面额应在 0~5000 元之间")
    if body.commission_base is not None:
        body.commission_base = _LEGACY_COMMISSION_BASES.get(
            body.commission_base, body.commission_base)
    if body.commission_base is not None and body.commission_base not in COMMISSION_BASES:
        errors.append("佣金基数取值不合法")
    if body.tier_discounts is not None:
        for key, rate in body.tier_discounts.items():
            try:
                if not (0.1 <= float(rate) <= 1.0):
                    errors.append(f"满 {key} 件折扣应在 1 折~10 折之间")
            except (TypeError, ValueError):
                errors.append(f"满 {key} 件折扣不是数字")
    return errors


@router.post("/policy")
def update_policy(body: PolicyUpdateRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    errors = _validate(body)
    if errors:
        raise HTTPException(status_code=400, detail="；".join(errors))
    row = _ensure_default(db)
    for field in ("name", "commission_rate", "commission_base", "ship_fee",
                  "freight_dom", "freight_cover", "coupon_borne_by",
                  "tier_discounts", "profit_floor_pct", "default_coupon"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)
    row.tier_discounts = {str(k): float(v) for k, v in (row.tier_discounts or {}).items()}
    db.commit()
    db.refresh(row)
    return {"saved": True, "config": _policy_payload(row), "promo_defaults": _promo_defaults(row)}


@router.post("/policy/reset")
def reset_policy(db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = _ensure_default(db)
    defaults = agent_service.policy().get("defaults", {})
    row.commission_rate = defaults.get("commission_rate", 0.06)
    row.commission_base = defaults.get("commission_base", "gmv_after_coupon")
    row.ship_fee = defaults.get("ship_fee", 19.9)
    row.freight_dom = defaults.get("freight_dom", 19.9)
    row.freight_cover = defaults.get("freight_cover", 0.0)
    row.coupon_borne_by = defaults.get("coupon_borne_by", "merchant")
    row.tier_discounts = defaults.get("tier_discounts", {"2": 0.8})
    row.profit_floor_pct = float(agent_service.policy().get("profit_floor_pct", 5.0))
    row.default_coupon = float(defaults.get("default_coupon", 29.0))
    db.commit()
    db.refresh(row)
    return {"reset": True, "config": _policy_payload(row), "promo_defaults": _promo_defaults(row)}
