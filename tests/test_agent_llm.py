# -*- coding: utf-8 -*-
"""真 Agent（工具循环）的回归测试：数字必须来自引擎、口径不全先问、越权必拒。

用假模型替换 llm，只验证编排逻辑与刹车，不联网。
"""
import unittest

from tests import _stubs

_stubs.install_all()

from app.models import Product  # noqa: E402
from app.services import agent as agent_service  # noqa: E402
from app.services.agent_skill import AgentAction  # noqa: E402


class P:
    def __init__(self, pid, name, price, cost_price, brand=""):
        self.id = pid
        self.name = name
        self.brand = brand
        self.price = price
        self.cost_price = cost_price
        self.category = ""
        self.selling_points = []


def make_db(products):
    db = _stubs.FakeDB()
    db.tables.setdefault("Product", []).extend(products)
    return db


class FakeLLM:
    """按脚本吐出 action，模拟模型的多步决策。"""

    is_mock = False
    base_url = "https://api.deepseek.com/v1"

    def __init__(self, script):
        self.script = list(script)
        self.prompts = []

    def complete_json(self, system, user, schema, temperature=0.7):
        self.prompts.append(user)
        node = self.script.pop(0) if self.script else {"action": "answer", "reply": "好的"}
        return AgentAction(**node)


class AgentLoopTest(unittest.TestCase):
    def setUp(self):
        self._origin = agent_service.llm

    def tearDown(self):
        agent_service.llm = self._origin

    def _run(self, script, message, products=None):
        agent_service.llm = FakeLLM(script)
        db = make_db(products or [P(1, "SR-72", 298.0, 238.0)])
        return db, agent_service.handle(db, "t-1", message, 1)

    def test_tool_then_answer_returns_bill_from_engine(self):
        _, out = self._run(
            [
                {"action": "tool", "tool": "calc_profit",
                 "args": {"product_id": 1, "qty": 2, "tier_rate": 0.8, "coupon": 29}},
                {"action": "answer", "reply": "两件八折叠加 29 元券，每件亏 56.26 元，比红线低。"},
            ],
            "SR-72 两件八折加 29 元券还赚吗",
        )
        self.assertEqual(out["action"], "bill")
        self.assertEqual(out["bill"]["利润"], -56.26)
        self.assertEqual(out["verdict"], "risk")
        self.assertFalse(out["fallback"])
        self.assertEqual(out["trace"][0]["tool"], "calc_profit")

    def test_fabricated_number_falls_back_to_engine_text(self):
        _, out = self._run(
            [
                {"action": "tool", "tool": "calc_profit",
                 "args": {"product_id": 1, "qty": 2, "tier_rate": 0.8, "coupon": 29}},
                {"action": "answer", "reply": "这套活动每件亏 99 元，建议立刻停掉。"},
            ],
            "算一下",
        )
        self.assertEqual(out["action"], "bill")
        self.assertTrue(out["fallback"])
        self.assertNotIn("99", out["reply"])
        self.assertIn("56.26", out["reply"])

    def test_ask_when_cost_missing(self):
        db = make_db([P(2, "K270", 369.99, None)])
        agent_service.llm = FakeLLM([
            {"action": "tool", "tool": "calc_profit", "args": {"product_id": 2, "qty": 2}},
            {"action": "ask", "reply": "还差一个口径：",
             "ask": ["这款的进货价是多少？"]},
        ])
        out = agent_service.handle(db, "t-2", "K270 两件八折能卖吗", 2)
        self.assertEqual(out["action"], "clarify")
        self.assertIn("进货价", out["reply"] + out["clarify"][0]["question"])

    def test_refuse_write_action(self):
        _, out = self._run(
            [{"action": "refuse", "reply": "改价得你自己在后台点，我只做测算。"}],
            "帮我把 SR-72 价格改成 280",
        )
        self.assertEqual(out["action"], "refuse")

    def test_unknown_tool_can_recover(self):
        _, out = self._run(
            [
                {"action": "tool", "tool": "make_it_up", "args": {}},
                {"action": "answer", "reply": "我重新查一下。"},
            ],
            "看看这款",
        )
        self.assertEqual(out["action"], "talk")
        self.assertEqual(out["trace"][0]["tool"], "make_it_up")

    def test_scan_tool_returns_store_table(self):
        _, out = self._run(
            [
                {"action": "tool", "tool": "store_scan",
                 "args": {"qty": 2, "tier_rate": 0.8, "coupon": 29}},
                {"action": "answer", "reply": "这套活动铺全店，三款都跌破红线。"},
            ],
            "把两件八折加 29 券铺到全店看看",
            products=[P(1, "SR-72", 298.0, 238.0), P(2, "J20", 288.0, 228.0)],
        )
        self.assertEqual(out["action"], "talk")
        self.assertEqual(out["trace"][0]["tool"], "store_scan")


class ToolGuardTest(unittest.TestCase):
    def test_run_tool_refuses_unknown_and_reports_missing(self):
        db = make_db([P(1, "SR-72", 298.0, 238.0)])
        self.assertIn("error", agent_service.run_tool(db, "nope", {}))
        out = agent_service.run_tool(db, "calc_profit", {"product_id": 1, "qty": 1})
        self.assertEqual(out["bill"]["利润"], 40.93)

    def test_numbers_whitelist_only_from_tools(self):
        bag = set()
        agent_service._collect_numbers({"bill": {"利润": -56.26}}, bag)
        self.assertTrue(agent_service._reply_ok("每件亏 56.26 元", bag))
        self.assertFalse(agent_service._reply_ok("每件亏 99 元", bag))


if __name__ == "__main__":
    unittest.main()
