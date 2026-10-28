# -*- coding: utf-8 -*-
"""推荐售价的口径边界：它**不含**店内营销活动。

这条测试是为了防止"把推荐价当成做了活动还能赚的价"——
评委最容易问的一句也是这个：你这个推荐价，活动叠上去还赚吗？

跑法：

    cd ai-marketing-ops
    python -m unittest tests.test_recommend_price -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import pricing  # noqa: E402


class RecommendPriceScopeTest(unittest.TestCase):
    def setUp(self):
        self.r = pricing.recommend_price(
            unit_cost=238.0, margin_pct=20.0,
            commission=0.06, commission_base="gmv_after_coupon",
            ship_fee=19.9, freight_dom=19.9, freight_intl=0.0,
        )

    def test_caliber_says_no_promotion(self):
        cal = self.r["口径"]
        self.assertIs(cal["含促销活动"], False)
        for word in ("满件折扣", "优惠券"):
            self.assertIn(word, cal["不含"], f"口径说明里必须点明不含 {word}")

    def test_reminder_text_present(self):
        tip = self.r["提醒"]
        self.assertTrue(tip)
        self.assertIn("利润防护", tip, "提醒里要指向按活动测算的入口")

    def test_only_one_caliber_key(self):
        """曾经的 bug：dict 里出现两个 "口径"，后者把前者的"不含活动"覆盖掉。"""
        keys = [k for k in self.r.keys() if k == "口径"]
        self.assertEqual(len(keys), 1)
        self.assertIn("含促销活动", self.r["口径"])

    def test_price_is_target_margin_when_no_promotion(self):
        """没有活动时，按推荐价卖，毛利率确实等于期望值。"""
        self.assertAlmostEqual(self.r["实际毛利率"], 20.0, places=1)

    def test_promotion_makes_it_negative(self):
        """同一款商品，一旦叠上八折+29 券，利润就翻负——这就是"要标明"的理由。"""
        bill = pricing.run(pricing.PricingInput(
            sku="FX9672 SR-72", price=298.0, qty=2, unit_cost=238.0,
            freight_dom=19.9, ship_fee=19.9, commission=0.06,
            tier_rate=0.8, coupon=29.0,
        ))
        self.assertLess(bill["利润"], 0)
        self.assertAlmostEqual(bill["利润"], -56.26, places=2)


if __name__ == "__main__":
    unittest.main()
