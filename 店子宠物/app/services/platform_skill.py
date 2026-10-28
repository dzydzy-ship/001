"""平台宣传 skill 装配：把 config/platform_skill_<platform>.md 注入生成提示词。

分工：
- `config/platform_skill_*.md`：写给模型看的平台写作规范（算法、结构、标题公式、钩子、红线）；
- `config/platforms.yaml`：机器可读的硬性限制（标题长度、标签数、禁用词、结构骨架）；
- 本文件：加载 + mtime 缓存，改 md 立即生效。

和 `agent_skill.py` 一个思路：行为规范放 md，代码只负责装配。
"""
from pathlib import Path
from typing import Dict

from ..config import CONFIG_DIR

SKILL_DIR = CONFIG_DIR
_cache: Dict[str, dict] = {}


def skill_path(platform: str) -> Path:
    return SKILL_DIR / f"platform_skill_{platform}.md"


def has_skill(platform: str) -> bool:
    return skill_path(platform).is_file()


def load_platform_skill(platform: str) -> str:
    """读取平台 skill 文件；文件不存在返回空串，改动后自动重载。"""
    path = skill_path(platform)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ""
    cached = _cache.get(platform)
    if cached and cached.get("mtime") == mtime:
        return cached.get("text", "")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return cached.get("text", "") if cached else ""
    _cache[platform] = {"mtime": mtime, "text": text}
    return text
