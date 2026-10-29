"""全局配置：从环境变量读取，YAML 配置从 config/ 目录加载。"""
import os
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
FRONTEND_DIR = BASE_DIR / "frontend"


class Settings:
    def __init__(self) -> None:
        # 大模型（OpenAI 兼容）
        self.llm_api_key = os.getenv("LLM_API_KEY", "").strip()
        # 默认指向 DeepSeek：国内直连、便宜，和设置页里默认选中的服务商一致
        self.llm_base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
        self.llm_model = os.getenv("LLM_MODEL", "deepseek-chat")

        # 数据库：默认 SQLite，生产环境换成 PostgreSQL 连接串即可
        self.database_url = os.getenv(
            "DATABASE_URL", f"sqlite:///{(DATA_DIR / 'app.db').as_posix()}"
        )

        # 热点采集
        self.hotspot_interval_minutes = int(os.getenv("HOTSPOT_INTERVAL_MINUTES", "15"))

    @property
    def use_mock_llm(self) -> bool:
        """未配置 API Key 时使用内置 Mock，保证演示可运行。"""
        return self.llm_api_key == ""


settings = Settings()


def load_yaml(name: str) -> dict:
    """加载 config/ 目录下的 YAML 配置。"""
    path = CONFIG_DIR / name
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
