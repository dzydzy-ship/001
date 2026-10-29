# -*- coding: utf-8 -*-
"""提取 index.html 里的 <script> 内容，写成 UTF-8 文件供 node --check 校验。

PowerShell 的 Get-Content/Set-Content 往返会把中文写成乱码，
所以这一步固定用 python 的 UTF-8 读写。
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
m = re.search(r"<script>(.*)</script>", html, re.S)
if not m:
    raise SystemExit("没找到 <script> 块")
out = ROOT / ".tmp2"
out.mkdir(exist_ok=True)
target = out / "check.js"
target.write_text(m.group(1), encoding="utf-8")
print(f"js chars: {len(m.group(1))} -> {target}")
