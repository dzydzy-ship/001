"""大模型客户端：OpenAI 兼容接口 + 结构化输出。

- 配置 API Key 后走真实模型，要求按 Pydantic JSON Schema 输出。
- 未配置时 is_mock=True，各服务自行提供规则化 Mock 输出（见各 service）。
"""
import json
import re
from typing import Optional, Type, TypeVar

import httpx
from pydantic import BaseModel

from .config import settings

T = TypeVar("T", bound=BaseModel)


class LLMClient:
    def __init__(self) -> None:
        self.api_key = settings.llm_api_key
        self.base_url = settings.llm_base_url
        self.model = settings.llm_model

    @property
    def is_mock(self) -> bool:
        return self.api_key == ""

    # ---- 运行时配置（设置页用；不落库，重启即清空） ----
    def reconfigure(self, api_key: Optional[str] = None,
                    base_url: Optional[str] = None,
                    model: Optional[str] = None) -> None:
        """就地换 Key / 地址 / 模型，下一个请求立即生效，不用重启服务。"""
        if api_key is not None:
            self.api_key = api_key.strip()
        if base_url is not None and base_url.strip():
            self.base_url = base_url.strip().rstrip("/")
        if model is not None and model.strip():
            self.model = model.strip()

    def clear(self) -> None:
        """回到环境变量里的配置（没有 Key 就是 Mock）。"""
        self.api_key = settings.llm_api_key
        self.base_url = settings.llm_base_url
        self.model = settings.llm_model

    def describe(self) -> dict:
        """给前端看的状态：只回 Key 的掩码，永远不回完整 Key。"""
        key = self.api_key or ""
        masked = f"{key[:5]}…{key[-4:]}" if len(key) > 12 else ("已配置" if key else "未配置")
        return {
            "base_url": self.base_url,
            "model": self.model,
            "key_masked": masked,
            "has_key": bool(key),
            "is_mock": self.is_mock,
        }

    def ping(self) -> dict:
        """最小连通性测试：只要模型回一句话，不校验内容。"""
        import time

        started = time.time()
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "只回复 ok 两个字母"}],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=25) as client:
                resp = client.post(f"{self.base_url}/chat/completions",
                                   headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            return {
                "ok": True,
                "ms": int((time.time() - started) * 1000),
                "model": data.get("model", self.model),
                "reply": (text or "").strip()[:80],
            }
        except Exception as exc:  # 错误原文要原样给前端，方便排查是 Key 还是网络
            return {
                "ok": False,
                "ms": int((time.time() - started) * 1000),
                "error": f"{type(exc).__name__}: {exc}",
            }

    def complete_json(
        self, system: str, user: str, schema: Type[T], temperature: float = 0.7
    ) -> T:
        """调用大模型并校验为指定 Pydantic 结构。仅约束输出结构，不保证事实正确。"""
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        payload = {
            "model": self.model,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": (
                        f"{user}\n\n请严格按以下 JSON Schema 输出，只输出 JSON，"
                        f"不要包含代码块或其他文字：\n{schema_json}"
                    ),
                },
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=120) as client:
            resp = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return schema.model_validate(self._extract_json(content))

    @staticmethod
    def _extract_json(text: str) -> dict:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                return json.loads(m.group(0))
            raise ValueError(f"无法解析 LLM 输出为 JSON: {text[:200]}")


llm = LLMClient()
