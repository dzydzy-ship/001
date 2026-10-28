"""模块一步骤 5：内容校验——事实、合规、格式。程序化检查，不依赖大模型。"""
import re

from ..config import load_yaml
from ..models import Product
from ..schemas import ContentGenerate

# 「最」作为极限词只有出现在“最+形容词”里才算违规（如 最好/最低/最便宜），
# “最怕/最近/最多”这类日常表达不拦截，避免误报。
_SUPERLATIVE = re.compile(
    r"最(?:好|佳|大|小|低|高|便宜|实惠|划算|受欢迎|值得|先进|专业|强|快|耐用|安全|健康|舒适|新|火|爆|牛|顶|优|美|帅|厉害)"
)
# 「第一」只在排名语境拦截：“第一名/第一品牌”或“销量第一/全网第一”；“第一步/第一次”不拦截。
_FIRST_SUFFIX = re.compile(r"第一(?:名|品牌|销量|口碑|选择|好|大)")
_FIRST_PREFIX = re.compile(r"(?:全国|全网|行业|国内|全球|销量|排名)第一")


def _forbidden_hits(text: str, words: set) -> list:
    hits = []
    for w in words:
        if w == "最":
            m = _SUPERLATIVE.search(text)
            if m:
                hits.append(m.group(0))
        elif w == "第一":
            m = _FIRST_SUFFIX.search(text) or _FIRST_PREFIX.search(text)
            if m:
                hits.append(m.group(0))
        elif w and w in text:
            hits.append(w)
    return hits


def audit_content(content: ContentGenerate, product: Product, platform_cfg: dict) -> dict:
    """对生成内容做程序化校验，返回 {passed, notes}。事实与合规仍需人工最终确认。"""
    notes: list = []
    risk_cfg = load_yaml("risk_keywords.yaml")

    # 1) 违规/夸大表述
    forbidden = set(platform_cfg.get("forbidden_words", [])) | set(risk_cfg.get("claim_keywords", []))
    text = " ".join([content.title, content.body, " ".join(content.tags), content.cta])
    for hit in _forbidden_hits(text, forbidden):
        notes.append(f"含禁用/夸大表述「{hit}」")

    # 2) 格式限制
    max_len = platform_cfg.get("title_max_length")
    if max_len and len(content.title) > max_len:
        notes.append(f"标题超过平台限制（{len(content.title)}/{max_len}）")
    tags_max = platform_cfg.get("tags_max", 0)
    if content.tags and tags_max and len(content.tags) > tags_max:
        notes.append(f"标签数量超过平台限制（{len(content.tags)}/{tags_max}）")

    # 3) 敏感话题命中（内容里出现负面/敏感词则提示复核）
    for w in risk_cfg.get("sensitive_topics", []):
        if w in text:
            notes.append(f"可能涉及敏感话题「{w}」，建议人工复核")

    if not notes:
        notes.append("通过基础校验（事实与合规请人工最终确认）")

    passed = all("通过" in n for n in notes)
    return {"passed": passed, "notes": notes}
