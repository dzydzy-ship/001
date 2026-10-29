# -*- coding: utf-8 -*-
"""设置与成本档案的回归测试（不需要真实第三方依赖）。

覆盖三件事：
1. 成本档案能存能取，且引擎优先用它覆盖 Product.cost_price；
2. 掩码永不出完整 Key；
3. 换 Key 之后 is_mock 跟着变、clear() 能回退。
"""
import unittest

from tests import _stubs

_stubs.install_all()

from app.llm import LLMClient  # noqa: E402
from app.models import ProductCost, PromoConfig  # noqa: E402
from app.services import agent as agent_service  # noqa: E402
from app.services import costs as cost_service  # noqa: E402
from app.schemas import AgentPlan  # noqa: E402


class FakeProduct:
    """够用就好：build_input 只读 id / name / price / cost_price。"""

    def __init__(self, pid=1, name="SR-72", price=298.0, cost_price=238.0):
        self.id = pid
        self.name = name
        self.price = price
        self.cost_price = cost_price


def make_config():
    return PromoConfig(
        name="默认口径", platform="taobao",
        commission_rate=0.06, commission_base="gmv_after_coupon",
        ship_fee=19.9, freight_dom=19.9, freight_cover=0.0,
        coupon_borne_by="merchant", tier_discounts={"2": 0.8},
        profit_floor_pct=5.0, is_default=True,
    )


class CostArchiveTest(unittest.TestCase):
    def test_missing_returns_empty(self):
        db = _stubs.FakeDB()
        self.assertEqual(cost_service.get_cost(db, 1), {})
        self.assertEqual(cost_service.get_cost(db, None), {})

    def test_upsert_then_read(self):
        db = _stubs.FakeDB()
        cost_service.upsert_cost(db, 7, {"unit_cost": 500.0, "freight_intl": 550.0,
                                         "freight_dom": 19.9})
        got = cost_service.get_cost(db, 7)
        self.assertEqual(got["unit_cost"], 500.0)
        self.assertEqual(got["freight_intl"], 550.0)

    def test_upsert_updates_existing_row(self):
        db = _stubs.FakeDB()
        cost_service.upsert_cost(db, 7, {"unit_cost": 500.0})
        cost_service.upsert_cost(db, 7, {"unit_cost": 480.0})
        self.assertEqual(len(db.tables.get("ProductCost", [])), 1)
        self.assertEqual(cost_service.get_cost(db, 7)["unit_cost"], 480.0)


class CostPrecedenceTest(unittest.TestCase):
    """商品档案优先于 Product.cost_price；本句话又优先于商品档案。"""

    def test_archive_overrides_product_column(self):
        db = _stubs.FakeDB()
        cost_service.upsert_cost(db, 1, {"unit_cost": 200.0, "freight_intl": 550.0})
        inp, missing = agent_service.build_input(
            FakeProduct(), {}, make_config(), AgentPlan(), db=db)
        self.assertEqual(missing, [])
        self.assertEqual(inp.unit_cost, 200.0)
        self.assertEqual(inp.freight_intl, 550.0)

    def test_utterance_beats_archive(self):
        db = _stubs.FakeDB()
        cost_service.upsert_cost(db, 1, {"unit_cost": 200.0})
        inp, _ = agent_service.build_input(
            FakeProduct(), {}, make_config(), AgentPlan(unit_cost=250.0), db=db)
        self.assertEqual(inp.unit_cost, 250.0)

    def test_falls_back_to_product_column(self):
        db = _stubs.FakeDB()
        inp, _ = agent_service.build_input(
            FakeProduct(), {}, make_config(), AgentPlan(), db=db)
        self.assertEqual(inp.unit_cost, 238.0)

    def test_missing_cost_still_reported(self):
        db = _stubs.FakeDB()
        inp, missing = agent_service.build_input(
            FakeProduct(cost_price=None), {}, make_config(), AgentPlan(), db=db)
        self.assertIn("unit_cost", missing)


class ProviderCatalogTest(unittest.TestCase):
    """设置页只提供三家服务商，地址与默认模型由后端给，Key 由用户填。"""

    def test_only_three_providers(self):
        from app.routers.settings import LLM_PROVIDERS
        keys = [p["key"] for p in LLM_PROVIDERS]
        self.assertEqual(keys, ["deepseek", "openai", "qwen"])
        for item in LLM_PROVIDERS:
            self.assertTrue(item["label"])
            self.assertTrue(item["base_url"].startswith("https://"))
            self.assertTrue(item["model"])
            self.assertNotIn("chat/completions", item["base_url"])

    def test_match_provider_by_base_url(self):
        from app.routers.settings import match_provider
        self.assertEqual(match_provider("https://api.deepseek.com/v1"), "deepseek")
        self.assertEqual(
            match_provider("https://dashscope.aliyuncs.com/compatible-mode/v1"), "qwen")
        self.assertEqual(match_provider("https://api.openai.com/v1"), "openai")
        self.assertEqual(match_provider(""), "")
        self.assertEqual(match_provider("https://my-own-proxy.example.com/v1"), "")


class PromoDefaultsTest(unittest.TestCase):
    """体检默认活动（满几件/几折/券多少）必须由口径推导，不能写死在前端。"""

    def test_defaults_follow_tier_and_coupon(self):
        row = PromoConfig(
            name="默认口径", platform="taobao",
            commission_rate=0.06, commission_base="gmv_after_coupon",
            ship_fee=19.9, freight_dom=19.9, freight_cover=0.0,
            coupon_borne_by="merchant", tier_discounts={"2": 0.9},
            profit_floor_pct=5.0, default_coupon=15.0,
        )
        from app.routers.settings import _promo_defaults
        data = _promo_defaults(row)
        self.assertEqual(data["qty"], 2)
        self.assertAlmostEqual(data["tier"], 0.9)
        self.assertAlmostEqual(data["coupon"], 15.0)

    def test_missing_coupon_falls_back_to_29(self):
        row = PromoConfig(
            name="x", platform="taobao",
            commission_rate=0.06, commission_base="gmv_after_coupon",
            ship_fee=19.9, freight_dom=19.9, freight_cover=0.0,
            coupon_borne_by="merchant", tier_discounts={"2": 0.8},
            profit_floor_pct=5.0, default_coupon=None,
        )
        from app.routers.settings import _promo_defaults
        self.assertAlmostEqual(_promo_defaults(row)["coupon"], 29.0)

    def test_coupon_range_guard(self):
        from app.routers.settings import _validate
        from app.schemas import PolicyUpdateRequest
        self.assertTrue(_validate(PolicyUpdateRequest(default_coupon=-1)))
        self.assertTrue(_validate(PolicyUpdateRequest(default_coupon=99999)))
        self.assertFalse(_validate(PolicyUpdateRequest(default_coupon=29)))

    def test_coupon_change_moves_guard(self):
        """券面额从 29 降到 5，SR-72 两件八折的利润必须跟着变。"""
        from app.services import pricing
        base = dict(price=298.0, qty=2, unit_cost=238.0, freight_intl=0.0,
                    freight_dom=19.9, freight_cover=0.0, tier_rate=0.8,
                    commission=0.06, commission_base="gmv_after_coupon",
                    ship_fee=19.9)
        lo = pricing.run(pricing.PricingInput(sku="SR-72", coupon=5.0, **base))
        hi = pricing.run(pricing.PricingInput(sku="SR-72", coupon=29.0, **base))
        self.assertGreater(lo["利润"], hi["利润"])
        # 券额少 24 元，利润少 24×(1−6%)：佣金按成交额抽，券也把佣金基数压低了
        self.assertAlmostEqual(hi["利润"] - lo["利润"], -24.0 * 0.94, places=2)


class LLMKeyTest(unittest.TestCase):
    def test_mask_never_leaks_full_key(self):
        client = LLMClient()
        client.reconfigure(api_key="sk-demo-0123456789abcd")  # 假的占位 Key，别填真实 Key
        desc = client.describe()
        self.assertTrue(desc["has_key"])
        self.assertFalse(desc["is_mock"])
        self.assertNotIn("0123456789", desc["key_masked"])
        self.assertIn("abcd", desc["key_masked"])

    def test_clear_back_to_mock(self):
        client = LLMClient()
        client.reconfigure(api_key="sk-test-1234567890abcd")
        self.assertFalse(client.is_mock)
        client.clear()
        self.assertTrue(client.is_mock)

    def test_base_url_trimmed(self):
        client = LLMClient()
        client.reconfigure(api_key="k", base_url=" https://api.deepseek.com/v1/ ")
        self.assertEqual(client.base_url, "https://api.deepseek.com/v1")

    def test_ping_without_network_returns_error(self):
        client = LLMClient()
        client.reconfigure(api_key="sk-test", base_url="https://example.invalid/v1")
        result = client.ping()
        self.assertFalse(result["ok"])
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
