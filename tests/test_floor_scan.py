# -*- coding: utf-8 -*-
"""A（每款独立毛利率红线）+ C（全店体检）的回归测试。"""
import unittest

from tests import _stubs

_stubs.install_all()

from app.models import Product  # noqa: E402
from app.services import agent as agent_service  # noqa: E402
from app.services import costs as cost_service  # noqa: E402


class P:
    """够 store_scan / guard_product 用的小商品对象。"""

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


class FloorPerProductTest(unittest.TestCase):
    def test_product_floor_wins(self):
        db = make_db([P(1, "SR-72", 298.0, 238.0)])
        cost_service.upsert_cost(db, 1, {"profit_floor_pct": 12.0})
        self.assertEqual(agent_service.floor_for_product(db, db.get(Product, 1)), 12.0)

    def test_falls_back_to_global(self):
        db = make_db([P(1, "SR-72", 298.0, 238.0)])
        floor = agent_service.floor_for_product(db, db.get(Product, 1))
        self.assertEqual(floor, 5.0)

    def test_guard_uses_own_floor(self):
        """毛利率 13.7%：红线 5% 时算合格，红线 20% 时算风险。"""
        db = make_db([P(1, "SR-72", 298.0, 238.0)])
        ok = agent_service.guard_product(db, 1, qty=1, tier_rate=1.0)
        self.assertEqual(ok["verdict"], "ok")
        cost_service.upsert_cost(db, 1, {"profit_floor_pct": 20.0})
        strict = agent_service.guard_product(db, 1, qty=1, tier_rate=1.0)
        self.assertEqual(strict["verdict"], "risk")
        self.assertAlmostEqual(strict["bill"]["利润率"], 13.7, places=1)


class StoreScanTest(unittest.TestCase):
    def setUp(self):
        # SR-72 薄利 13.7%，赛斯纳 24.1%，K270 缺成本
        self.db = make_db([
            P(1, "SR-72", 298.0, 238.0),
            P(3, "赛斯纳 182", 328.0, 228.0),
            P(4, "K270 黑鹰", 369.99, None),
        ])

    def test_sorted_by_margin_and_flags(self):
        result = agent_service.store_scan(self.db, qty=2, tier_rate=0.8, coupon=29.0)
        items = result["items"]
        self.assertEqual(len(items), 3)
        margins = [i.get("margin") for i in items if i.get("margin") is not None]
        self.assertEqual(margins, sorted(margins, reverse=True))
        self.assertEqual(items[-1]["status"], "missing")
        self.assertEqual(result["summary"]["missing"], 1)

    def test_risk_and_ok_both_present(self):
        result = agent_service.store_scan(self.db, qty=2, tier_rate=0.8, coupon=29.0)
        statuses = {i["name"]: i["status"] for i in result["items"]}
        self.assertEqual(statuses["赛斯纳 182"], "risk")
        self.assertEqual(statuses["SR-72"], "risk")

    def test_headroom_negative_when_under_floor(self):
        result = agent_service.store_scan(self.db, qty=2, tier_rate=0.8, coupon=29.0)
        sr = [i for i in result["items"] if i["name"] == "SR-72"][0]
        self.assertLess(sr["headroom"], 0)
        self.assertLess(sr["profit"], 0)

    def test_no_promotion_scan_is_healthy(self):
        result = agent_service.store_scan(self.db, qty=1, tier_rate=1.0, coupon=0.0)
        statuses = {i["name"]: i["status"] for i in result["items"]}
        self.assertEqual(statuses["SR-72"], "ok")
        self.assertEqual(statuses["赛斯纳 182"], "ok")


if __name__ == "__main__":
    unittest.main()
