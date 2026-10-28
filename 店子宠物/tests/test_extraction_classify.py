"""结构化提取「更聪明」测试：价格四类、来源、置信度、分类、待确认提示。"""
import unittest

from app.services.extraction import extract_product


class ExtractionClassifyTest(unittest.TestCase):
    def test_price_roles_and_confidence(self):
        raw = """产品名称：316不锈钢保温杯
品牌：为例杯业
品类：保温杯
出厂价：20元
零售控价：39.9元
批发控价：28元
促销价：35.9元
容量：500ml
材质：316不锈钢
卖点：保温、便携、防漏
适用场景：通勤、户外
证书：SGS 检测报告
"""
        ex = extract_product(raw)
        self.assertEqual(ex.name, "316不锈钢保温杯")
        self.assertEqual(ex.brand, "为例杯业")
        self.assertEqual(ex.cost_price, 20)
        self.assertEqual(ex.price, 39.9)
        # 价格四类
        self.assertEqual(ex.price_roles.get("成本"), 20)
        self.assertEqual(ex.price_roles.get("标价"), 39.9)
        self.assertEqual(ex.price_roles.get("批发"), 28)
        self.assertEqual(ex.price_roles.get("促销"), 35.9)
        # 来源与置信度
        self.assertEqual(ex.field_sources.get("name"), "原文显式")
        self.assertGreaterEqual(ex.confidence.get("name", 0), 0.9)
        self.assertEqual(ex.confidence.get("brand", 0), 0.95)
        # 分类
        self.assertEqual(ex.classified.get("price"), "价格类")
        self.assertEqual(ex.classified.get("cost_price"), "价格类")
        self.assertEqual(ex.classified.get("specs"), "规格类")
        self.assertEqual(ex.classified.get("selling_points"), "营销类")
        # 待确认提示
        self.assertTrue(any("批发指导价" in r for r in ex.review))
        self.assertTrue(any("促销价" in r for r in ex.review))
        # 规格里不应再混入价格键
        for bad in ("出厂价", "零售控价", "批发控价", "促销价"):
            self.assertNotIn(bad, ex.specs)

    def test_marketing_words_not_evidence(self):
        raw = """商品名称：静音机械键盘
卖点：最好用的键盘，行业领先
证书：质检报告
"""
        ex = extract_product(raw)
        self.assertTrue(any("营销话术" in r for r in ex.review))
        joined = " ".join(ex.evidence)
        self.assertNotIn("最好用", joined)

    def test_missing_price_review(self):
        raw = "商品名称：无价商品\n容量：1L"
        ex = extract_product(raw)
        self.assertIsNone(ex.price)
        self.assertIsNone(ex.cost_price)
        self.assertTrue(any("没有明确价格" in r for r in ex.review))
        self.assertIn("价格", ex.missing_fields)

    def test_wholesale_used_as_price_fallback(self):
        raw = "商品名称：渠道款保温杯\n批发控价：28元"
        ex = extract_product(raw)
        self.assertEqual(ex.price, 28)
        self.assertTrue(any("批发指导价" in r for r in ex.review))

    def test_empty_material(self):
        ex = extract_product("   ")
        self.assertTrue(ex.missing_fields)
        self.assertTrue(ex.review)


if __name__ == "__main__":
    unittest.main()
