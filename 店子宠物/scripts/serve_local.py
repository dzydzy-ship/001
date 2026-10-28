# -*- coding: utf-8 -*-
"""本地起服务（不依赖 venv，直接把 .libs 加进 sys.path）。

    python scripts/serve_local.py [端口]

默认 8010。没有配 LLM_API_KEY 时自动走 Mock 模式：
测算、追问、拒绝、归因全部可用，只有措辞由模板生成而非大模型。
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
LIBS = os.path.join(HERE, ".libs")
if os.path.isdir(LIBS):
    sys.path.insert(0, LIBS)

import uvicorn  # noqa: E402

from app.main import app  # noqa: E402


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8010
    print(f"serving on http://127.0.0.1:{port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
