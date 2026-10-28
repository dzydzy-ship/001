# -*- coding: utf-8 -*-
"""佣金基数口径的测试。

两档到底差在哪：
- `gmv_after_coupon`（默认）：佣金按**券后**成交额抽，平台把商家的券当成降价；
- `gmv`：佣金按**折后、券前**的成交额抽，券由商家自己承担，平台照原价抽佣。

差额恒等于「券额 × 佣金率」，与件数、折扣无关。

跑法：

    cd ai-marketing-ops
    python -m unittest tests.test_commission_base -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.pricing import (  # noqa: E402
    COMMISSION_BASES,
    PricingError,
    PricingInput,
    normalize_commission_base,
    run,
)

# SR-72：标价 298、进价 238，两件八折 + 29 元券（与文档里的真账一致）
SR72 = dict(sku="FX9672 SR-72", price=298.0, qty=2, unit_cost=238.0,
            freight_dom=19.9, ship_fee=19.9, commission=0.06,
            tier_rate=0.8, coupon=29.0)


class CommissionBaseTest(unittest.TestCase):
    def test_two_bases_only(self):
        """下拉只有两档，第三档不许再出现。"""
        self.assertEqual(COMMISSION_BASES, ("gmv_after_coupon", "gmv"))

    def test_after_coupon_is_default(self):
        r = run(PricingInput(**SR72))
        # 券后：成交额 447.80，佣金基数含买家运费 → 467.70
        self.assertAlmostEqual(r["佣金基数"], 467.70, places=2)
        self.assertAlmostEqual(r["平台佣金"], 28.06, places=2)
        self.assertAlmostEqual(r["利润"], -56.26, places=2)

    def test_before_coupon_costs_more(self):
        r = run(PricingInput(commission_base="gmv", **SR72))
        self.assertAlmostEqual(r["佣金基数"], 496.70, places=2)
        self.assertAlmostEqual(r["平台佣金"], 29.80, places=2)
        self.assertAlmostEqual(r["利润"], -58.00, places=2)
        # 差额＝券额 × 佣金率
        self.assertAlmostEqual(29.0 * 0.06, 29.80 - 28.06, places=2)

    def test_legacy_value_no_longer_breaks(self):
        """老库里存过 gmv_with_ship_fee，不能因此算不出账。"""
        legacy = run(PricingInput(commission_base="gmv_with_ship_fee", **SR72))
        normal = run(PricingInput(**SR72))
        self.assertAlmostEqual(legacy["利润"], normal["利润"], places=2)
        self.assertEqual(normalize_commission_base("gmv_with_ship_fee"), "gmv_after_coupon")

    def test_unknown_value_still_rejected(self):
        with self.assertRaises(PricingError):
            run(PricingInput(commission_base="whatever", **SR72))

    def test_ship_fee_always_in_base(self):
        """两档都把买家运费算进佣金基数——所以"含运费"不该是一个选项。"""
        for base in COMMISSION_BASES:
            r = run(PricingInput(commission_base=base, **SR72))
            self.assertAlmostEqual(r["佣金基数"] - (r["成交额"] if base == "gmv_after_coupon"
                                                    else r["折扣后金额"]), 19.9, places=2)


if __name__ == "__main__":
    unittest.main()
