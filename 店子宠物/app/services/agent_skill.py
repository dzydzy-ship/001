"""Agent skill 装配：把 config/agent_skill.md 变成可执行的行为规范 + 工具协议。

分工很清楚：
- `config/agent_skill.md`：写给模型看的规矩（角色、铁律、协议、话术），改行为改这里；
- 本文件：工具清单、JSON 协议结构、提示词拼装；
- `app/services/agent.py`：真正执行工具循环、调引擎、校验数字。

数字永远由 `pricing.py` 算，模型只负责"选工具、说人话"。
"""
import json
from typing import Any, Dict, List

from ..config import CONFIG_DIR

SKILL_FILE = CONFIG_DIR / "agent_skill.md"
MAX_STEPS = 4

_FALLBACK_SKILL = (
    "你是商家侧的促销利润顾问。数字只能来自工具返回的结果，不得自行计算或推测；"
    "口径缺失时先追问；不得代替商家改价发券。"
)

_cache: Dict[str, Any] = {"mtime": None, "text": _FALLBACK_SKILL}

TOOL_SPECS: List[Dict[str, Any]] = [
    {
        "name": "list_products",
        "desc": "列出店铺商品：名称、标价、进货成本有没有、这款自己的毛利率红线。",
        "args": {},
    },
    {
        "name": "calc_profit",
        "desc": "算一套促销组合的账。返回成交额、买家运费、商品成本、跨国运费、国内运费、"
                "平台佣金、利润、利润率、毛利红线、是否跌破、每条可选调整方案、促销叠加阶梯。",
        "args": {
            "product_id": "商品 id（整数，可选）",
            "sku": "商品名关键词（可选，与 product_id 二选一）",
            "qty": "件数（整数，默认 1）",
            "tier_rate": "折扣率：八折 0.8、七五折 0.75、不打折 1",
            "coupon": "券面额（元）",
            "price": "标价（元，可不填，默认取商品标价）",
            "unit_cost": "进货单价（元，可不填，默认取成本档案）",
            "freight_intl": "跨国运费/件（元）",
            "freight_dom": "国内运费/单（元）",
        },
    },
    {
        "name": "store_scan",
        "desc": "把同一套活动铺到全店，返回每款的毛利率、自身红线、还能让多少价、判定。",
        "args": {
            "qty": "件数（整数）",
            "tier_rate": "折扣率（如 0.8）",
            "coupon": "券面额（元）",
        },
    },
    {
        "name": "get_policy",
        "desc": "返回当前计价口径：平台佣金率、佣金基数、国内运费、包邮覆盖、"
                "满件折扣表、毛利率红线、默认券面额。",
        "args": {},
    },
    {
        "name": "get_cost",
        "desc": "返回某款商品的成本档案：进货单价、跨国运费、国内运费、这款自己的毛利率红线。",
        "args": {"product_id": "商品 id（整数）"},
    },
]


def load_skill() -> str:
    """读技能文件；文件被改动后自动重载（演示时可以直接改 md 看效果）。"""
    try:
        mtime = SKILL_FILE.stat().st_mtime
    except OSError:
        return _FALLBACK_SKILL
    if _cache["mtime"] != mtime:
        try:
            _cache["text"] = SKILL_FILE.read_text(encoding="utf-8")
            _cache["mtime"] = mtime
        except OSError:
            return _FALLBACK_SKILL
    return _cache["text"] or _FALLBACK_SKILL


def tools_catalog() -> str:
    return json.dumps(TOOL_SPECS, ensure_ascii=False, indent=2)


def system_prompt() -> str:
    return (
        f"{load_skill()}\n\n"
        "## 可用工具（参数说明）\n"
        f"{tools_catalog()}\n\n"
        "## 输出格式（每次都必须是一个 JSON 对象）\n"
        '{"thought":"一句话","action":"tool|ask|answer|refuse",'
        '"tool":"工具名","args":{},"reply":"给商家看的话","ask":["问题"]}\n'
        "只输出 JSON，不要代码块、不要解释。"
    )


try:  # 真实环境用 pydantic；测试替身里也能跑
    from pydantic import BaseModel, Field

    class AgentAction(BaseModel):
        thought: str = Field(default="")
        action: str = Field(default="answer")
        tool: str = Field(default="")
        args: Dict[str, Any] = Field(default_factory=dict)
        reply: str = Field(default="")
        ask: List[str] = Field(default_factory=list)

except Exception:  # pragma: no cover
    AgentAction = None  # type: ignore
