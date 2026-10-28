# -*- coding: utf-8 -*-
"""促销测算引擎的单元测试。

只用标准库，所以没有装 fastapi/pydantic 也能跑：

    cd ai-marketing-ops
    python -m unittest tests.test_pricing -v

这些测试同时也是"账怎么算"的可执行说明书——每个断言都对应一句口径。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.pricing import (  # noqa: E402
    PricingError,
    PricingInput,
    export_price_by_margin,
    gmv_required,
    run,
    scan,
    suggest_options,
)

# 真实一款：FX9672 SR-72，出厂价 238，店铺标价 298
SR72 = dict(sku="FX9672 SR-72", price=298.0, unit_cost=238.0,
            freight_dom=19.9, ship_fee=19.9, commission=0.06)


class TestEngineBasics(unittest.TestCase):
    def test_no_promotion(self):
        """单件不促销：利润 40.93，毛利率 13.7%。"""
        r = run(PricingInput(**SR72))
        self.assertAlmostEqual(r["成交额"], 298.0, places=2)
        self.assertAlmostEqual(r["订单实收"], 317.9, places=2)
        self.assertAlmostEqual(r["平台佣金"], 19.07, places=2)
        self.assertAlmostEqual(r["利润"], 40.93, places=2)
        self.assertAlmostEqual(r["利润率"], 13.7, places=1)

    def test_coupon_only(self):
        """只发 29 元券：利润掉到 13.67（5.1%），商家容易以为"还行"。"""
        r = run(PricingInput(coupon=29.0, **SR72))
        self.assertAlmostEqual(r["成交额"], 269.0, places=2)
        self.assertAlmostEqual(r["利润"], 13.67, places=2)
        self.assertAlmostEqual(r["利润率"], 5.1, places=1)

    def test_tier_only(self):
        """只做两件八折：已经亏 29.00，此时券都还没用上。"""
        r = run(PricingInput(qty=2, tier_rate=0.8, **SR72))
        self.assertAlmostEqual(r["成交额"], 476.80, places=2)
        self.assertAlmostEqual(r["商品成本"], 476.0, places=2)
        self.assertAlmostEqual(r["利润"], -29.00, places=2)
        self.assertAlmostEqual(r["利润率"], -6.1, places=1)
        self.assertTrue(any("盖不住进货成本" in w for w in r["警告"]))

    def test_tier_plus_coupon(self):
        """两件八折 + 29 元券：亏 56.26（-12.6%），这是作品的开场案例。"""
        r = run(PricingInput(qty=2, tier_rate=0.8, coupon=29.0, **SR72))
        self.assertAlmostEqual(r["成交额"], 447.80, places=2)
        self.assertAlmostEqual(r["订单实收"], 467.70, places=2)
        self.assertAlmostEqual(r["平台佣金"], 28.06, places=2)
        self.assertAlmostEqual(r["利润"], -56.26, places=2)
        self.assertAlmostEqual(r["利润率"], -12.6, places=1)

    def test_commission_base_switch(self):
        """换佣金基数口径，结果必然不同——所以口径必须由商家确认。"""
        a = run(PricingInput(coupon=29.0, commission_base="gmv_after_coupon", **SR72))
        b = run(PricingInput(coupon=29.0, commission_base="gmv", **SR72))
        self.assertGreater(b["平台佣金"], a["平台佣金"])


class TestGuardRails(unittest.TestCase):
    def test_missing_cost_raises(self):
        """缺进货成本就报错，不允许按 0 算出一个漂亮但不真实的利润。"""
        with self.assertRaises(PricingError):
            run(PricingInput(sku="X", price=298.0))

    def test_bad_tier_rate(self):
        with self.assertRaises(PricingError):
            run(PricingInput(tier_rate=1.5, **SR72))

    def test_bad_commission_base(self):
        with self.assertRaises(PricingError):
            run(PricingInput(commission_base="whatever", **SR72))


class TestBreakEven(unittest.TestCase):
    def test_max_coupon_single_unit(self):
        """单件不促销时，要保住 5% 毛利率最多只能发 29.24 元券。

        也就是说店里那张 ¥29 券已经贴着红线——这正是"看起来没事"的根源。
        """
        data = suggest_options(PricingInput(**SR72), floor_pct=5.0)
        self.assertAlmostEqual(data["最多可发券"], 29.24, delta=0.05)
        self.assertFalse(data["是否跌破红线"])

    def test_two_items_cannot_be_saved_by_coupon(self):
        """两件八折下，券额再怎么调都救不回来：折扣本身就是亏损源。"""
        data = suggest_options(PricingInput(qty=2, tier_rate=0.8, coupon=29.0, **SR72),
                              floor_pct=5.0)
        self.assertTrue(data["是否跌破红线"])
        self.assertLess(data["最多可发券"], 0)
        # 调整方案必须按利润从高到低排，商家先看到"最不亏"的那个
        profits = [o["利润"] for o in data["可选调整"]]
        self.assertEqual(profits, sorted(profits, reverse=True))

    def test_gmv_required_matches_run(self):
        """反解出来的成交额，回代进引擎必须正好等于目标利润率。"""
        inp = PricingInput(**SR72)
        need = gmv_required(inp, 0.05)
        back = run(PricingInput(price=need / 1, qty=1, coupon=0.0,
                                commission_base="gmv_after_coupon",
                                freight_dom=19.9, ship_fee=19.9,
                                commission=0.06, unit_cost=238.0))
        self.assertAlmostEqual(back["利润率"], 5.0, delta=0.1)


class TestExportPricing(unittest.TestCase):
    def test_margin_pricing(self):
        """出口按利润率定价：成本 489、跨国运费 550、汇率 6.7 → 20% 毛利对应 186.32 USD。"""
        r = export_price_by_margin(489.0, 0.20)
        self.assertAlmostEqual(r["售价USD"], 186.32, delta=0.05)


class TestBatchScan(unittest.TestCase):
    def test_scan_flags_losers(self):
        rows = [
            {"sku": "FX9672", "price": 298.0, "unit_cost": 238.0},
            {"sku": "FX9630", "price": 288.0, "unit_cost": 228.0},
            {"sku": "FX9601", "price": 328.0, "unit_cost": 228.0},
        ]
        out = scan(rows, floor_pct=5.0,
                   defaults={"commission": 0.06, "freight_dom": 19.9, "ship_fee": 19.9})
        self.assertEqual(out["总数"], 3)
        self.assertEqual(len(out["明细"]), 3)
        # 排序后利润率最低的一定排在最前，商家先看"最不能打折"的款
        rates = [r["利润率"] for r in out["明细"]]
        self.assertEqual(rates, sorted(rates))


if __name__ == "__main__":
    unittest.main()
