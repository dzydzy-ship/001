# -*- coding: utf-8 -*-
"""本地起服务（不依赖 venv，直接把 .libs 加进 sys.path）。

    python scripts/serve_local.py [端口]

端口默认 8010，也可用环境变量 APP_PORT 指定；监听地址默认 0.0.0.0，
也就是同一局域网的其他电脑 / 手机也能打开（只想本机访问就设 APP_HOST=127.0.0.1）。
没有配 LLM_API_KEY 时自动走 Mock 模式：测算、追问、拒绝、归因全部可用，
只有措辞由模板生成而非大模型。
"""
import os
import socket
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
LIBS = os.path.join(HERE, ".libs")
if os.path.isdir(LIBS):
    sys.path.insert(0, LIBS)

import uvicorn  # noqa: E402

from app.main import app  # noqa: E402


def lan_ips():
    """返回本机局域网 IPv4 地址（方便打印给同网段的其他设备用）。"""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return [ip for ip in sorted(ips) if not ip.startswith(("127.", "169.254."))]


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.getenv("APP_PORT", "8010"))
    host = os.getenv("APP_HOST", "0.0.0.0")
    print(f"serving on http://127.0.0.1:{port}  (bind {host}:{port})", flush=True)
    if host == "0.0.0.0":
        for ip in lan_ips():
            print(f"  LAN: http://{ip}:{port}", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
