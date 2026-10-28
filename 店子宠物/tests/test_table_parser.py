"""通用表格解析引擎测试：用合成表格覆盖 一维表 / 块表 / 合并单元格 / 缺价 / 空表 / 坏文件。"""
import os
import tempfile
import unittest

from openpyxl import Workbook

from app.services.table_parser import parse_file


def make_xlsx(rows, merges=(), title="Sheet1"):
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for row in rows:
        ws.append(list(row))
    for m in merges:
        ws.merge_cells(m)
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    wb.save(path)
    return path


class TableParserTest(unittest.TestCase):
    def setUp(self):
        self.paths = []

    def tearDown(self):
        for p in self.paths:
            try:
                os.unlink(p)
            except OSError:
                pass

    def _parse(self, rows, merges=()):
        path = make_xlsx(rows, merges)
        self.paths.append(path)
        return parse_file(path)

    def test_flat_table(self):
        rows = [
            ["货号", "商品名称", "售价", "成本价", "卖点", "规格"],
            ["A1", "316不锈钢保温杯", 39.9, 20, "保温、便携", "容量：500ml\n材质：316不锈钢"],
            ["A2", "静音机械键盘", 199, 120, "静音、手感好", "配列：87键"],
        ]
        preview = self._parse(rows)
        self.assertTrue(preview.ok)
        self.assertEqual(preview.table_type, "一维表")
        self.assertEqual(len(preview.products), 2)
        first = preview.products[0]
        self.assertEqual(first.name, "316不锈钢保温杯")
        self.assertEqual(first.cost_price, 20)
        self.assertEqual(first.price, 39.9)
        self.assertIn("容量", first.specs)
        self.assertIn("材质", first.specs)

    def test_block_table_with_merges(self):
        rows = [
            [], [],
            ["飞熊玩具 产品参数表"],
            [],
            ["货号", "产品图片", "产品规格", "", "放产", "标配", "扫描看视频", "详情页",
             "卖点", "单品重量", "出厂价", "批发控价", "零售控价"],
            ["FX622", "a.jpg",
             "产品货号：FX622\n产品名称：FX622 遥控飞机\n产品特点：耐摔、耐玩\n飞机尺寸：42cm",
             "", "500", "飞机+遥控器+电池", "v", "d", "耐摔耐玩", "260g", 88, 128, 199],
            ["", "", "产品颜色：红/蓝", "", "", "", "", "", "", "", "", "", ""],
            ["", "", "毛重：520g", "", "", "", "", "", "", "", "", "", ""],
            ["FX801", "b.jpg",
             "产品货号：FX801\n产品名称：FX801 遥控飞机\n产品特点：耐撞\n飞机尺寸：50cm",
             "", "300", "飞机+遥控器", "v", "d", "耐撞", "300g", 120, 160, 259],
        ]
        merges = ["A6:A8", "K6:K8", "L6:L8", "M6:M8"]
        preview = self._parse(rows, merges)
        self.assertTrue(preview.ok)
        self.assertEqual(preview.table_type, "多行块表")
        self.assertEqual(len(preview.products), 2)
        first, second = preview.products
        self.assertEqual(first.name, "FX622 遥控飞机")
        self.assertEqual(first.sku, "FX622")
        self.assertEqual(first.cost_price, 88)
        self.assertEqual(first.price, 199)
        self.assertEqual(first.specs.get("飞机尺寸"), "42cm")
        self.assertEqual(first.specs.get("产品颜色"), "红/蓝")
        self.assertEqual(first.specs.get("毛重"), "520g")
        self.assertEqual(second.name, "FX801 遥控飞机")
        self.assertEqual(second.cost_price, 120)

    def test_missing_price(self):
        rows = [["货号", "商品名称", "卖点"], ["X1", "无价商品", "好用"]]
        preview = self._parse(rows)
        self.assertEqual(len(preview.products), 1)
        self.assertIn("售价", preview.products[0].missing)
        self.assertIn("成本价", preview.products[0].missing)

    def test_duplicate_sku_kept_separate(self):
        rows = [
            ["货号", "商品名称", "零售价"],
            ["FX622", "迷彩款", 199],
            ["FX622", "普通款", 189],
        ]
        preview = self._parse(rows)
        self.assertEqual(len(preview.products), 2)
        self.assertEqual({p.name for p in preview.products}, {"迷彩款", "普通款"})

    def test_empty_sheet(self):
        preview = self._parse([[], []])
        self.assertEqual(preview.products, [])
        self.assertTrue(any("空" in w for w in preview.warnings))

    def test_unsupported_suffix(self):
        fd, path = tempfile.mkstemp(suffix=".txt")
        os.close(fd)
        self.paths.append(path)
        with open(path, "w", encoding="utf-8") as f:
            f.write("x")
        preview = parse_file(path)
        self.assertFalse(preview.ok)
        self.assertIn("不支持", preview.error)


if __name__ == "__main__":
    unittest.main()
