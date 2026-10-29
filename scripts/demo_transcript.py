# -*- coding: utf-8 -*-
"""把 Agent 的真实运行结果导出成一段示例对话（可直接贴进作品/PPT）。

    cd ai-marketing-ops
    python scripts/demo_transcript.py

说明：
    它跑的是真实代码——parse_plan → pricing.run → narrate，中间没有任何硬编码台词。
    环境里缺 fastapi/sqlalchemy 等依赖也没关系，测试替身只在脚本内生效。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests import _stubs  # noqa: E402

_stubs.install_all()

from app.models import Product  # noqa: E402
from app.seed import REAL_SKUS  # noqa: E402
from app.services import agent  # noqa: E402

TURNS = [
    "我打算上两件八折，再发 29 元券，卖 SR-72 会亏吗？",
    "把 29 元券去掉，只做两件八折呢？",
    "那把券降到 15 元呢？",
    "能不能帮我把价格直接改到 280？",
    "换个商品，K270 黑鹰做同样的活动呢？",
    "它进价还没定，按 300 算",
    "不促销的话利润是多少？",
]


def build_db():
    db = _stubs.FakeDB()
    for item in REAL_SKUS:
        db.add(Product(**item))
    db.commit()
    for row in db.tables["Product"]:
        db.refresh(row)
    return db


def main() -> None:
    db = build_db()
    lines = [
        "# 示例对话（真实运行结果）",
        "",
        "> 由 `scripts/demo_transcript.py` 直接跑引擎生成，回答里的每个数字都来自确定性计算，",
        "> 未使用任何预写台词。口径：佣金 6%、买家运费 19.9 元（收付相抵）、满两件八折、利润红线 5%。",
        "",
    ]
    session = "demo-1"
    for index, question in enumerate(TURNS, start=1):
        out = agent.handle(db, session, question)
        lines.append(f"**商家**：{question}")
        lines.append("")
        lines.append(f"**Agent**（{out['action']}）：{out['reply']}")
        lines.append("")
        if out.get("action") == "bill":
            bill = out["bill"]
            lines.append("| 指标 | 数值 |")
            lines.append("| --- | --- |")
            for key in ("成交额", "单件成交价", "商品成本", "国内运费", "平台佣金", "利润", "利润率"):
                if key in bill:
                    lines.append(f"| {key} | {bill[key]} |")
            lines.append("")
            lines.append("| 归因（引擎复算） | 利润 | 毛利率 |")
            lines.append("| --- | --- | --- |")
            for row in out["ladder"]:
                lines.append(f"| {row['方案']} | {row['利润']} | {row['利润率']}% |")
            lines.append("")
            if out.get("options"):
                lines.append("| 可选调整 | 利润 | 利润率 | 说明 |")
                lines.append("| --- | --- | --- | --- |")
                for option in out["options"]:
                    lines.append(
                        f"| {option['名称']} | {option['利润']} | {option['利润率']}% | {option['说明']} |"
                    )
                lines.append("")
        lines.append("---")
        lines.append("")

    text = "\n".join(lines)
    out_path = os.path.join(ROOT, "..", "..", "competition", "05_示例对话.md")
    out_path = os.path.abspath(out_path)
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    print(text)
    print("saved", out_path)


if __name__ == "__main__":
    main()
