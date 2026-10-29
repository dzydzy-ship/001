"""通用商品表格解析引擎。

设计目标：不绑定任何一家商家的表结构，靠「找表头行 + 列词语料 + 样本值特征」
推断列义，靠「主键分组 + 合并单元格」切商品块，靠「键：值」抽取规格参数。

支持的表格形态：
1. 标准一维表：一行一个商品（列头明确）；
2. 多行块表：一个商品占多行，首行放主信息、后续行放「键：值」规格（如飞熊玩具表）；
3. 合并单元格：主键/价格列纵向合并，值会下发给块内每一行用于取值；
4. 多 sheet：每个 sheet 独立解析，空 sheet 跳过。

对外只暴露 parse_file()：输入文件路径，输出 ImportPreview（商品草稿列表 + 解析元信息）。
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..schemas import ImportPreview, ProductDraft


@dataclass
class TableSheetInfo:
    """单个 sheet 的原始内容（只在解析过程中使用，不对外序列化）。"""

    name: str
    rows: List[List[str]] = field(default_factory=list)
    merged: List[Tuple[int, int, int, int]] = field(default_factory=list)

# ---------------------------------------------------------------------------
# 一、列词语料：同义列名 → 标准字段
# ---------------------------------------------------------------------------
COLUMN_LEXICON: Dict[str, List[str]] = {
    "sku": ["货号", "型号", "编号", "商品编码", "产品编码", "款号", "料号", "sku", "product id"],
    "name": ["商品名称", "产品名称", "商品名", "产品名", "品名", "名称", "标题"],
    "category": ["品类", "类目", "分类", "类别", "产品类型", "所属类目"],
    "cost_price": ["出厂价", "成本价", "进货价", "供货价", "成本", "进价", "采购价"],
    "price": ["零售控价", "零售价", "售价", "标价", "天猫价", "京东价", "零售", "价格", "单价"],
    "wholesale_price": ["批发控价", "批发价", "批发", "渠道价", "分销价"],
    "selling_points": ["卖点", "产品特点", "特点", "亮点", "核心卖点", "优势", "主打"],
    "specs": ["产品规格", "规格", "参数", "商品规格", "规格参数"],
    "package": ["标配", "包装清单", "配件", "装箱清单", "随附"],
    "evidence": ["证书", "产品证书", "认证", "检测报告", "资质"],
    "image": ["产品图片", "图片", "主图", "图片链接", "图"],
    "weight": ["单品重量", "重量", "毛重", "净重"],
    "stock": ["库存", "放产", "现货", "数量"],
}

# 字段中文名（用于提示与前端展示）
FIELD_LABELS = {
    "sku": "货号", "name": "商品名称", "category": "品类",
    "cost_price": "成本价", "price": "售价", "wholesale_price": "批发价",
    "selling_points": "卖点", "specs": "规格", "package": "包装清单",
    "evidence": "资质证据", "image": "图片", "weight": "重量", "stock": "库存",
}

# 「键：值」行的键词表（键 → 归属字段；不在表里的键统一进 specs）
KV_KEY_FIELDS: Dict[str, str] = {
    "产品货号": "sku", "货号": "sku", "型号": "sku", "产品编码": "sku",
    "产品名称": "name", "商品名称": "name", "产品名": "name", "名称": "name",
    "产品特点": "selling_points", "卖点": "selling_points", "特点": "selling_points",
    "产品证书": "evidence", "证书": "evidence", "认证": "evidence",
    "标配": "package", "包装清单": "package",
    "单价": "price", "零售价": "price", "售价": "price", "标价": "price",
    "出厂价": "cost_price", "成本价": "cost_price", "进货价": "cost_price",
}

# 键值行：形如「飞机尺寸：42cm」「飞行时间：10分钟左右」
_KV_RE = re.compile(r"^([^\s:：][^:：]{0,15})[:：]\s*(.+)$")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")

MAX_HEADER_SCAN = 25       # 表头最多扫描前 25 行
MAX_SHEET_PRODUCTS = 500   # 单个 sheet 最多解析商品数（防超大表拖垮服务）

# 备注/说明/联系方式行：不是商品，遇到就跳过
NOTE_PREFIXES = (
    "备注", "说明", "注意", "温馨提示", "更新时间", "联系人", "手机", "电话",
    "地址", "网址", "扫一扫", "以下产品", "注：", "注:",
)


# ---------------------------------------------------------------------------
# 二、读取表格（.xls 走 xlrd，.xlsx/.xlsm 走 openpyxl）
# ---------------------------------------------------------------------------
def read_sheets(path: str) -> List[TableSheetInfo]:
    """读取工作簿所有 sheet，返回原始行 + 合并单元格 + 填充后的行。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".xls":
        return _read_xls(path)
    if ext in (".xlsx", ".xlsm"):
        return _read_xlsx(path)
    raise ValueError(f"暂不支持的文件格式：{ext or '未知'}（支持 .xls / .xlsx）")


def _clean(value: Any) -> str:
    """清洗单元格：保留换行（块表的「键：值」常靠换行分隔），只去掉行尾空白。"""
    if value is None:
        return ""
    if isinstance(value, float):
        # 整数浮点显示成整数（价格 12.0 → 12）
        return str(int(value)) if value.is_integer() else f"{value:g}"
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.strip() for line in text.split("\n"))
    return text.strip()


def _one_line(text: str) -> str:
    """把多行文本压成一行（用于名称/货号等单值字段）。"""
    return re.sub(r"\s+", " ", text or "").strip()


def _is_note_row(row: List[str]) -> bool:
    """判断一行是不是备注/说明/联系方式（这类行不能当商品块起点）。"""
    for cell in row:
        t = _one_line(cell)
        if not t:
            continue
        if any(t.startswith(p) for p in NOTE_PREFIXES):
            return True
    return False


def _read_xls(path: str) -> List[TableSheetInfo]:
    import xlrd  # 延迟导入：只有真正解析时才需要

    book = xlrd.open_workbook(path, on_demand=True)
    sheets: List[TableSheetInfo] = []
    for sh in book.sheets():
        rows: List[List[str]] = []
        for r in range(sh.nrows):
            rows.append([_clean(sh.cell_value(r, c)) for c in range(sh.ncols)])
        merged = [(int(rlo), int(rhi), int(clo), int(chi)) for rlo, rhi, clo, chi in sh.merged_cells]
        sheets.append(TableSheetInfo(name=sh.name, rows=rows, merged=merged))
    book.release_resources()
    return sheets


def _read_xlsx(path: str) -> List[TableSheetInfo]:
    import openpyxl

    book = openpyxl.load_workbook(path, data_only=True, read_only=False)
    sheets: List[TableSheetInfo] = []
    for ws in book.worksheets:
        rows: List[List[str]] = []
        for row in ws.iter_rows(values_only=True):
            rows.append([_clean(v) for v in row])
        merged = []
        for rng in ws.merged_cells.ranges:  # 1-based
            merged.append((rng.min_row - 1, rng.max_row, rng.min_col - 1, rng.max_col))
        sheets.append(TableSheetInfo(name=ws.title, rows=rows, merged=merged))
    book.close()
    return sheets


def fill_merged(rows: List[List[str]], merged: List[Tuple[int, int, int, int]]) -> List[List[str]]:
    """把合并单元格的值下发给区域内每个单元格（0-based，rhi/chi 独占）。"""
    filled = [list(r) for r in rows]
    for rlo, rhi, clo, chi in merged:
        if rlo >= len(filled) or clo >= len(filled[0]):
            continue
        value = filled[rlo][clo]
        for r in range(rlo, min(rhi, len(filled))):
            for c in range(clo, min(chi, len(filled[r]))):
                filled[r][c] = value
    return filled


# ---------------------------------------------------------------------------
# 三、表头识别 + 列义推断
# ---------------------------------------------------------------------------
def _match_field(text: str) -> Optional[Tuple[str, float]]:
    """把列名文本匹配到标准字段，返回 (字段, 置信度)。"""
    t = re.sub(r"\s+", "", text or "").lower()
    if not t:
        return None
    # 先精确，再包含；长词优先（避免「批发控价」被「控价」类误判，靠字段内排序保证）
    for field, words in COLUMN_LEXICON.items():
        for word in words:
            if t == word.lower():
                return field, 0.90
    for field, words in COLUMN_LEXICON.items():
        for word in words:
            if word.lower() in t:
                return field, 0.75
    return None


def detect_header(rows: List[List[str]]) -> Optional[int]:
    """找表头行：命中列词最多的「标题行」，且下方确实有数据。

    排除含「键：值」的行（那是数据行，如「产品货号：FX603」），
    并要求至少命中 2 个不同字段，避免把数据行误当表头。
    """
    best_idx, best_score = None, 0
    for idx in range(min(len(rows), MAX_HEADER_SCAN)):
        row = rows[idx]
        if any(("：" in c or ":" in c) for c in row):
            continue  # 键值数据行不是表头
        fields = set()
        for cell in row:
            matched = _match_field(cell)
            if matched:
                fields.add(matched[0])
        has_next = idx + 1 < len(rows) and any(c.strip() for c in rows[idx + 1])
        if len(fields) >= 2 and has_next and len(fields) > best_score:
            best_idx, best_score = idx, len(fields)
    return best_idx


def infer_columns(header: List[str]) -> Dict[int, Tuple[str, float]]:
    """列下标 → (标准字段, 置信度)。同一字段多列时保留第一个（价格列按词序已区分三档）。"""
    cols: Dict[int, Tuple[str, float]] = {}
    used: Dict[str, int] = {}
    for idx, cell in enumerate(header):
        matched = _match_field(cell)
        if not matched:
            continue
        field, conf = matched
        if field in used and field not in ("specs", "selling_points"):
            continue  # 非规格/卖点字段只取第一列
        used[field] = idx
        cols[idx] = (field, conf)
    return cols


# ---------------------------------------------------------------------------
# 四、切商品块
# ---------------------------------------------------------------------------
def _primary_col(cols: Dict[int, Tuple[str, float]]) -> Optional[int]:
    for idx, (field, _conf) in cols.items():
        if field == "sku":
            return idx
    for idx, (field, _conf) in cols.items():
        if field == "name":
            return idx
    return None


def split_blocks(rows: List[List[str]], header_idx: int, cols: Dict[int, Tuple[str, float]]) -> List[Tuple[int, int]]:
    """返回 [(起行, 止行)]；块表按主键分组，一维表每行一块，无主键时每行一块。"""
    data_start = header_idx + 1
    end = len(rows)
    while end > data_start and not any(c.strip() for c in rows[end - 1]):
        end -= 1  # 去掉尾部空行

    primary = _primary_col(cols)
    blocks: List[Tuple[int, int]] = []
    if primary is None:
        # 没有主键列：按「整行是否有内容」逐行成块
        for r in range(data_start, end):
            if any(c.strip() for c in rows[r]):
                blocks.append((r, r + 1))
        return blocks

    cur_start: Optional[int] = None
    for r in range(data_start, end):
        row = rows[r]
        key = row[primary].strip() if primary < len(row) else ""
        if _is_note_row(row):
            # 备注/说明行：结束当前块，且自身不成块
            if cur_start is not None:
                blocks.append((cur_start, r))
                cur_start = None
            continue
        if key:
            if cur_start is not None:
                blocks.append((cur_start, r))
            cur_start = r
        elif cur_start is None:
            # 首行主键为空但整行有内容 → 也算一个块起点
            if any(c.strip() for c in row):
                cur_start = r
    if cur_start is not None:
        blocks.append((cur_start, end))
    return blocks


def _headerless_blocks(rows: List[List[str]]) -> Optional[Tuple[int, List[Tuple[int, int]]]]:
    """没有表头时的兜底：找「重复出现短编号」的列（通常是货号），按它切块。

    返回 (列下标, blocks)；找不到返回 None。用于「定制」这类没有表头、
    首列直接写货号、右侧写「键：值」规格的 sheet。
    """
    if not rows:
        return None
    width = max((len(r) for r in rows), default=0)
    for c in range(min(width, 6)):
        vals = [_one_line(r[c]) for r in rows if c < len(r) and r[c].strip()]
        if len(vals) < 1:
            continue
        if any(len(v) > 24 or "：" in v or ":" in v for v in vals):
            continue
        if not any(re.search(r"[A-Za-z0-9]{2,}", v) for v in vals):
            continue
        blocks: List[Tuple[int, int]] = []
        cur: Optional[int] = None
        for r in range(len(rows)):
            if _is_note_row(rows[r]):
                if cur is not None:
                    blocks.append((cur, r))
                    cur = None
                continue
            key = rows[r][c].strip() if c < len(rows[r]) else ""
            if key:
                if cur is not None:
                    blocks.append((cur, r))
                cur = r
        if cur is not None:
            blocks.append((cur, len(rows)))
        if len(blocks) >= 1:
            return c, blocks
    return None


def _is_note_draft(draft: ProductDraft) -> bool:
    """备注/联系方式类的草稿（无价格无成本，且文本像说明）→ 丢弃。"""
    if draft.price is not None or draft.cost_price is not None:
        return False
    head = _one_line((draft.sku or "") + " " + (draft.name or ""))
    if any(head.startswith(p) for p in NOTE_PREFIXES):
        return True
    # 没有任何可识别字段、只有零散文字
    return not (draft.name or draft.sku or draft.specs or draft.raw_text.strip())


# ---------------------------------------------------------------------------
# 五、字段抽取
# ---------------------------------------------------------------------------
def _split_points(text: str) -> List[str]:
    parts = re.split(r"[、,，;；\n]|(?<=[。])", text or "")
    return [p.strip(" 。.;；") for p in parts if p and p.strip(" 。.;；")]


def _to_number(text: str) -> Optional[float]:
    m = _NUM_RE.search(text or "")
    return float(m.group(0)) if m else None


KV_SKIP_FIELDS = {"sku", "name", "category", "price", "cost_price", "wholesale_price", "image", "stock", "weight"}


def _kv_pairs(block_rows: List[List[str]], cols: Dict[int, Tuple[str, float]]) -> Dict[str, str]:
    """从块内所有文本里抓「键：值」。价格/名称等已由列取值，跳过；规格列要参与。

    一个单元格里可能有多行「键：值」（原表用换行分隔），按行逐个匹配。
    """
    skip_cols = {idx for idx, (field, _c) in cols.items() if field in KV_SKIP_FIELDS}
    kv: Dict[str, str] = {}
    for row in block_rows:
        for idx, cell in enumerate(row):
            if idx in skip_cols or not cell.strip():
                continue
            for line in cell.split("\n"):
                m = _KV_RE.match(line.strip())
                if not m:
                    continue
                key, value = m.group(1).strip(), _one_line(m.group(2))
                if len(key) > 16 or not value:
                    continue
                if key in kv and kv[key]:
                    continue
                kv[key] = value
    return kv


def _kv_field_value(key: str, value: str) -> Tuple[str, str]:
    """把「键：值」归类到 (字段, 子键)；返回字段名，sub 为空表示整字段。"""
    key_clean = key.strip()
    if key_clean in KV_KEY_FIELDS:
        return KV_KEY_FIELDS[key_clean], key_clean
    # 模糊命中（如「产品名称（中文）」）
    for k, field in KV_KEY_FIELDS.items():
        if k in key_clean:
            return field, key_clean
    return "specs", key_clean


def build_draft(
    rows: List[List[str]],
    filled: List[List[str]],
    start: int,
    stop: int,
    header_idx: int,
    cols: Dict[int, Tuple[str, float]],
    sheet_name: str,
) -> ProductDraft:
    header = rows[header_idx] if header_idx < len(rows) else []
    block_raw = rows[start:stop]
    block_filled = filled[start:stop]

    draft = ProductDraft(source_sheet=sheet_name, source_row=start + 1)
    # 显式新建容器：避免个别 BaseModel 实现（如测试桩）把默认容器当成共享对象
    draft.specs = {}
    draft.selling_points = []
    draft.package_list = []
    draft.evidence = []
    draft.confidence = {}
    draft.missing = []
    draft.warnings = []
    confidence: Dict[str, float] = {}
    kv = _kv_pairs(block_raw, cols)

    # 1) 先按列取值（列义来自表头词表）
    if not block_filled:
        block_filled = []
    for idx, (field, conf) in cols.items():
        max_cols = max((len(r) for r in block_filled), default=0)
        if idx >= max_cols:
            continue
        # 合并单元格/块表：首行可能是主信息，值也可能在块内任意一行
        value = ""
        for row in block_filled:
            if idx < len(row) and row[idx].strip():
                value = row[idx].strip()
                break
        if not value:
            continue
        if field == "cost_price":
            draft.cost_price = _to_number(value)
        elif field == "price":
            draft.price = _to_number(value)
        elif field == "wholesale_price":
            draft.wholesale_price = _to_number(value)
        elif field == "sku":
            draft.sku = _one_line(value)
        elif field == "name":
            draft.name = _one_line(value)
        elif field == "category":
            draft.category = _one_line(value)
        elif field == "selling_points":
            draft.selling_points.extend(_split_points(value))
        elif field == "package":
            draft.package_list.extend(_split_points(value))
        elif field == "evidence":
            draft.evidence.extend(_split_points(value))
        elif field == "image":
            draft.image_url = _one_line(value)
        elif field == "weight":
            draft.weight = _one_line(value)
        elif field == "stock":
            draft.specs["生产状态"] = _one_line(value)
        elif field == "specs":
            # 规格列里若已经是「键：值」多行文本，交给 _kv_pairs 拆；否则整段留档
            flat = _one_line(value)
            if flat and "：" not in flat and ":" not in flat:
                draft.specs.setdefault("规格", flat[:80])
        if value:
            confidence[field] = max(confidence.get(field, 0), conf)

    # 2) 再用「键：值」补字段（键值比列更精确）
    for key, value in kv.items():
        field, sub = _kv_field_value(key, value)
        conf = 0.95
        if field == "name":
            if not draft.name:
                draft.name = value
        elif field == "sku":
            if not draft.sku:
                draft.sku = value
        elif field == "selling_points":
            draft.selling_points.extend(_split_points(value))
        elif field == "package":
            draft.package_list.extend(_split_points(value))
        elif field == "evidence":
            draft.evidence.extend(_split_points(value))
        elif field in ("price", "cost_price"):
            num = _to_number(value)
            if field == "price" and draft.price is None:
                draft.price = num
            elif field == "cost_price" and draft.cost_price is None:
                draft.cost_price = num
        else:
            draft.specs[sub] = value
        confidence[field] = max(confidence.get(field, 0), conf)

    # 3) 名称兜底：无名称但 description/规格里有产品名称
    if not draft.name:
        for key in ("产品名称", "商品名称", "名称"):
            if key in draft.specs:
                draft.name = draft.specs.pop(key)
                confidence["name"] = 0.9
                break

    # 4) 原始文本（给商品资料留档）
    lines: List[str] = []
    for row in block_raw:
        cells = [_one_line(c) for c in row if c and c.strip()]
        if cells:
            lines.append(" | ".join(cells))
    draft.raw_text = "\n".join(lines)[:4000]

    draft.selling_points = _dedupe(draft.selling_points)[:8]
    draft.package_list = _dedupe(draft.package_list)[:12]
    draft.evidence = _dedupe(draft.evidence)[:8]

    # 5) 缺失项 + 置信度
    if not draft.name:
        draft.missing.append("商品名称")
    if draft.price is None:
        draft.missing.append("售价")
    if draft.cost_price is None:
        draft.missing.append("成本价")
    if not draft.specs:
        draft.missing.append("规格参数")
    draft.confidence = {FIELD_LABELS.get(k, k): round(v, 2) for k, v in confidence.items()}
    if draft.cost_price is not None and draft.price is not None and draft.cost_price > draft.price:
        draft.warnings.append("成本价高于售价，请核对是否读错列")
    return draft


def _dedupe(items: List[str]) -> List[str]:
    seen, out = set(), []
    for it in items:
        it = (it or "").strip()
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


# ---------------------------------------------------------------------------
# 六、表型判断 + 主流程
# ---------------------------------------------------------------------------
def detect_table_type(rows: List[List[str]], header_idx: int, blocks: List[Tuple[int, int]], cols: Dict[int, Tuple[str, float]]) -> str:
    if not blocks:
        return "空表"
    max_rows = max(stop - start for start, stop in blocks)
    has_spec_col = any(field in ("specs", "selling_points", "package") for field, _ in cols.values())
    if max_rows >= 3 and has_spec_col:
        return "多行块表"
    return "一维表"


def _build_drafts(
    rows: List[List[str]],
    filled: List[List[str]],
    blocks: List[Tuple[int, int]],
    header_idx: int,
    cols: Dict[int, Tuple[str, float]],
    sheet_name: str,
    warnings: List[str],
) -> List[ProductDraft]:
    """把每个块变成草稿；单块失败不影响整表，备注/说明块丢弃。"""
    drafts: List[ProductDraft] = []
    for start, stop in blocks[:MAX_SHEET_PRODUCTS]:
        try:
            draft = build_draft(rows, filled, start, stop, header_idx, cols, sheet_name)
        except Exception as exc:  # 单块失败不影响整表
            warnings.append(f"第 {start + 1} 行块解析失败：{exc}")
            continue
        if _is_note_draft(draft):
            continue
        if draft.name or draft.sku or draft.specs or draft.price is not None:
            drafts.append(draft)
    return drafts


def parse_sheet(sheet: TableSheetInfo) -> Tuple[str, int, Dict[int, str], List[ProductDraft], List[str]]:
    """解析单个 sheet，返回 (表型, 表头行号, 列映射, 草稿列表, 警告)。"""
    rows = sheet.rows
    warnings: List[str] = []
    if not rows or not any(any(c.strip() for c in row) for row in rows):
        return "空表", 0, {}, [], ["空 sheet，已跳过"]

    filled = fill_merged(rows, sheet.merged)
    header_idx = detect_header(rows)
    if header_idx is None:
        fallback = _headerless_blocks(rows)
        if fallback:
            col, blocks = fallback
            cols = {col: ("sku", 0.80)}
            drafts = _build_drafts(rows, filled, blocks, 0, cols, sheet.name, warnings)
            if drafts:
                warnings.append("没有找到表头行，已按「首列=货号」的块结构解析；价格/重量等列无法识别，可在预览里手动补齐")
                return "无表头块表", 0, {FIELD_LABELS["sku"]: "首列（自动识别）"}, drafts, warnings
        return "未识别表头", 0, {}, [], ["没有找到包含货号/名称/价格等列名的表头行"]

    header = rows[header_idx]
    cols = infer_columns(header)
    if not cols:
        return "未识别列", header_idx + 1, {}, [], ["表头行没有可识别的列名"]

    blocks = split_blocks(rows, header_idx, cols)
    drafts = _build_drafts(rows, filled, blocks, header_idx, cols, sheet.name, warnings)

    table_type = detect_table_type(rows, header_idx, blocks, cols)
    col_map = {FIELD_LABELS.get(field, field): header[idx] if idx < len(header) else "" for idx, (field, _c) in cols.items()}
    if not drafts:
        warnings.append("识别到表头但没有解析出商品行")
    return table_type, header_idx + 1, col_map, drafts, warnings


def parse_file(path: str) -> ImportPreview:
    """解析整个工作簿。"""
    filename = os.path.basename(path)
    try:
        sheets = read_sheets(path)
    except Exception as exc:
        return ImportPreview(filename=filename, ok=False, error=f"文件读取失败：{exc}")

    products: List[ProductDraft] = []
    warnings: List[str] = []
    sheet_names: List[str] = []
    table_type, header_row, col_map = "", 0, {}
    for sheet in sheets:
        sheet_names.append(sheet.name)
        try:
            stype, hrow, cmap, drafts, warns = parse_sheet(sheet)
        except Exception as exc:
            warnings.append(f"sheet「{sheet.name}」解析失败：{exc}")
            continue
        if not table_type and drafts:
            table_type, header_row, col_map = stype, hrow, cmap
        products.extend(drafts)
        warnings.extend(f"[{sheet.name}] {w}" for w in warns)

    return ImportPreview(
        filename=filename,
        ok=True,
        sheets=sheet_names,
        table_type=table_type or "未识别",
        header_row=header_row,
        columns=col_map,
        products=products,
        warnings=warnings,
    )


def parse_bytes(data: bytes, filename: str) -> ImportPreview:
    """解析上传文件的字节内容（写入临时文件后复用 parse_file）。"""
    suffix = os.path.splitext(filename or "upload.xlsx")[1].lower() or ".xlsx"
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return parse_file(tmp)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
