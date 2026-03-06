"""Semantic comparison: compare generated SQL vs handwritten SQL structure."""

import os
import re
from dataclasses import dataclass, field

from .excel_parser import ExcelParser
from .sql_writer import SQLWriter
from .case_dict import CaseDictExtractor


@dataclass
class SegmentInfo:
    """一个 INSERT...SELECT 段的结构信息"""
    insert_cols: list[str] = field(default_factory=list)
    select_exprs: list[str] = field(default_factory=list)
    from_tables: list[str] = field(default_factory=list)
    joins: list[str] = field(default_factory=list)
    where_count: int = 0
    has_group_by: bool = False
    # (col_index, target_field, source_field) for CASE expressions
    case_fields: list[tuple[int, str, str]] = field(default_factory=list)


def strip_comments(text: str) -> str:
    """去除 SQL 中的 -- 和 /* */ 注释（不处理引号内的）。"""
    out = []
    i = 0
    in_str = False
    in_block = False
    while i < len(text):
        ch = text[i]
        if in_block:
            if text[i:i+2] == '*/':
                in_block = False
                i += 2
            else:
                i += 1
            continue
        if ch == "'" and not in_str:
            in_str = True
        elif ch == "'" and in_str:
            in_str = False
        elif text[i:i+2] == '/*' and not in_str:
            in_block = True
            i += 2
            continue
        elif text[i:i+2] == '--' and not in_str:
            j = text.find('\n', i)
            if j < 0:
                break
            i = j
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def parse_select_exprs(select_text: str) -> list[str]:
    """按顶层逗号拆分 SELECT 表达式。"""
    cleaned = strip_comments(select_text)
    tokens = re.findall(r"'[^']*'|\b\w+\b|[(),]", cleaned)
    exprs: list[str] = []
    paren_depth = 0
    case_depth = 0
    last_split = 0
    pos = 0

    for token in tokens:
        tup = token.upper()
        if tup == '(':
            paren_depth += 1
        elif tup == ')':
            paren_depth -= 1
        elif tup == 'CASE':
            case_depth += 1
        elif tup == 'END':
            case_depth = max(0, case_depth - 1)
        elif token == ',' and paren_depth <= 0 and case_depth <= 0:
            idx = cleaned.find(',', pos)
            if idx >= 0:
                expr = cleaned[last_split:idx].strip()
                if expr:
                    exprs.append(expr)
                last_split = idx + 1
                pos = idx + 1
                continue
        idx = cleaned.find(token, pos)
        if idx >= 0:
            pos = idx + len(token)

    last = cleaned[last_split:].strip()
    if last:
        exprs.append(last)
    return exprs


def extract_case_info(expr: str) -> tuple[bool, str]:
    """判断表达式是否为 CASE，提取源字段名。返回 (is_case, source_field)。"""
    stripped = strip_comments(expr).strip()
    if not re.match(r'CASE\b', stripped, re.IGNORECASE):
        return False, ''
    m = re.search(r'\bT\d+\.(\w+)', stripped)
    if m:
        return True, m.group(1)
    m = re.search(r'WHEN\s+(\w+)', stripped, re.IGNORECASE)
    if m:
        return True, m.group(1)
    return True, '?'


def parse_sql_structure(sql_text: str) -> list[SegmentInfo]:
    """解析 SQL 文本，提取每个 INSERT...SELECT 段的结构信息。"""
    segments = []

    for m in re.finditer(
        r'insert\s+(?:/\*.*?\*/\s*)?into\s+[\w.]+\s*\(([^)]+)\)\s*\n?\s*select\s+(.+?)\n\s*from\s+(\S+)',
        sql_text, re.DOTALL | re.IGNORECASE,
    ):
        seg = SegmentInfo()
        clean_insert = strip_comments(m.group(1))
        seg.insert_cols = [
            c.strip() for c in clean_insert.split(',')
            if c.strip()
        ]

        select_text = m.group(2)
        seg.select_exprs = parse_select_exprs(select_text)
        seg.from_tables = [m.group(3)]

        rest_start = m.end()
        next_insert = re.search(r'\binsert\s+into\b', sql_text[rest_start:], re.IGNORECASE)
        rest_end = rest_start + next_insert.start() if next_insert else len(sql_text)
        rest = sql_text[rest_start:rest_end]

        for jm in re.finditer(r'(LEFT|RIGHT|INNER|CROSS)?\s*JOIN\s+(\S+)', rest, re.IGNORECASE):
            seg.joins.append(jm.group(2))
            seg.from_tables.append(jm.group(2))

        seg.where_count = len(re.findall(r'\bWHERE\b|\bAND\b', rest, re.IGNORECASE))
        seg.has_group_by = bool(re.search(r'\bGROUP\s+BY\b', rest, re.IGNORECASE))

        for i, expr in enumerate(seg.select_exprs):
            is_case, src_field = extract_case_info(expr)
            if is_case:
                col = seg.insert_cols[i] if i < len(seg.insert_cols) else f'?col{i}'
                seg.case_fields.append((i, col, src_field))

        segments.append(seg)

    return segments


def compare_segments(
    gen_segs: list[SegmentInfo],
    ref_segs: list[SegmentInfo],
    sheet_name: str,
) -> list[str]:
    """比较生成的和手写的段结构，返回差异列表。"""
    diffs = []

    if len(gen_segs) != len(ref_segs):
        diffs.append(
            f"段数不同: 生成={len(gen_segs)}, 手写={len(ref_segs)}"
        )

    for i in range(min(len(gen_segs), len(ref_segs))):
        g, r = gen_segs[i], ref_segs[i]
        prefix = f"段{i+1}"

        if len(g.insert_cols) != len(r.insert_cols):
            diffs.append(
                f"{prefix} INSERT列数: 生成={len(g.insert_cols)}, 手写={len(r.insert_cols)}"
            )

        if len(g.select_exprs) != len(r.select_exprs):
            diffs.append(
                f"{prefix} SELECT表达式数: 生成={len(g.select_exprs)}, 手写={len(r.select_exprs)}"
            )

        if len(g.insert_cols) != len(g.select_exprs):
            diffs.append(
                f"{prefix} 生成SQL列数不平衡: INSERT={len(g.insert_cols)}, SELECT={len(g.select_exprs)}"
            )
        if len(r.insert_cols) != len(r.select_exprs):
            diffs.append(
                f"{prefix} 手写SQL列数不平衡: INSERT={len(r.insert_cols)}, SELECT={len(r.select_exprs)}"
            )

        g_tables = {t.split('.')[-1].upper() for t in g.from_tables}
        r_tables = {t.split('.')[-1].upper() for t in r.from_tables}
        if g_tables != r_tables:
            only_gen = g_tables - r_tables
            only_ref = r_tables - g_tables
            if only_gen:
                diffs.append(f"{prefix} 仅生成有的表: {only_gen}")
            if only_ref:
                diffs.append(f"{prefix} 仅手写有的表: {only_ref}")

        if g.has_group_by != r.has_group_by:
            diffs.append(
                f"{prefix} GROUP BY: 生成={'有' if g.has_group_by else '无'}, "
                f"手写={'有' if r.has_group_by else '无'}"
            )

        g_case_cols = {col for _, col, _ in g.case_fields}
        r_case_cols = {col for _, col, _ in r.case_fields}

        missing_case = r_case_cols - g_case_cols
        if missing_case:
            details = []
            for _, col, src in r.case_fields:
                if col in missing_case:
                    details.append(f"{col}({src})")
            diffs.append(
                f"{prefix} 缺少CASE映射(手写有,生成无): {', '.join(details)}"
            )

        extra_case = g_case_cols - r_case_cols
        if extra_case:
            details = []
            for _, col, src in g.case_fields:
                if col in extra_case:
                    details.append(f"{col}({src})")
            diffs.append(
                f"{prefix} 多出CASE映射(生成有,手写无): {', '.join(details)}"
            )

        shared = g_case_cols & r_case_cols
        for col in sorted(shared):
            g_src = next(src for _, c, src in g.case_fields if c == col)
            r_src = next(src for _, c, src in r.case_fields if c == col)
            if g_src.upper() != r_src.upper():
                diffs.append(
                    f"{prefix} CASE源字段不同 {col}: 生成={g_src}, 手写={r_src}"
                )

    return diffs


def generate_sql_for_sheet(excel_file: str, sheet_name: str,
                           dict_dirs: list[str] | None = None) -> str | None:
    """Generate SQL for a single sheet. Returns SQL string or None on failure."""
    case_dict = None
    if dict_dirs:
        case_dict = CaseDictExtractor()
        for d in dict_dirs:
            case_dict.load_from_directory(d)

    ep = ExcelParser(excel_file, sheet_name)
    mapping = ep.parse()
    if not mapping:
        return None

    gen = SQLWriter(mapping, case_dict=case_dict)
    return gen.generate()


def run_comparison(excel_file: str, hand_written_dir: str,
                   dict_dirs: list[str] | None = None,
                   sheet_filter: str | None = None) -> tuple[int, int, int, list]:
    """Run comparison across all sheets. Returns (total, pass_count, diff_count, results)."""
    import openpyxl
    wb = openpyxl.load_workbook(excel_file, data_only=True)
    sheets = [s for s in wb.sheetnames if s != '目录']
    if sheet_filter:
        sheets = [s for s in sheets if sheet_filter in s]

    total = 0
    pass_count = 0
    diff_count = 0
    results = []

    for sheet_name in sheets:
        m = re.match(r'表(\d+)\.(\d+)', sheet_name)
        if not m:
            continue
        major, minor = m.group(1), m.group(2)
        num = f'{major}.{minor}'
        candidates = [
            os.path.join(hand_written_dir, f'{num}.txt'),
            os.path.join(hand_written_dir, f'PROC_T_{major}_{minor}.sql'),
            os.path.join(hand_written_dir, f'T_{major}_{minor}.sql'),
        ]
        ref_file = next((f for f in candidates if os.path.exists(f)), None)
        if not ref_file:
            continue

        total += 1

        gen_sql = generate_sql_for_sheet(excel_file, sheet_name, dict_dirs)
        if gen_sql is None:
            results.append((sheet_name, ['生成失败']))
            diff_count += 1
            continue

        with open(ref_file, encoding='utf-8') as f:
            ref_sql = f.read()

        gen_segs = parse_sql_structure(gen_sql)
        ref_segs = parse_sql_structure(ref_sql)
        diffs = compare_segments(gen_segs, ref_segs, sheet_name)

        if diffs:
            diff_count += 1
            results.append((sheet_name, diffs))
        else:
            pass_count += 1

    return total, pass_count, diff_count, results


def print_report(excel_file: str, hand_written_dir: str,
                 dict_dirs: list[str] | None,
                 total: int, pass_count: int, diff_count: int,
                 results: list):
    """Print comparison report to stdout."""
    print(f"{'='*70}")
    print(f"语义对照测试报告")
    print(f"Excel: {excel_file}")
    print(f"手写SQL: {hand_written_dir}")
    print(f"字典: {', '.join(dict_dirs) if dict_dirs else '无'}")
    print(f"{'='*70}")
    print(f"总计: {total} 个 sheet, 通过: {pass_count}, 有差异: {diff_count}")
    print()

    if results:
        diff_types: dict[str, int] = {}
        for sheet_name, diffs in results:
            for d in diffs:
                dtype = d.split(':')[0].strip()
                dtype = re.sub(r'^段\d+\s+', '', dtype)
                diff_types[dtype] = diff_types.get(dtype, 0) + 1

        print("--- 差异类型汇总 ---")
        for dtype, count in sorted(diff_types.items(), key=lambda x: -x[1]):
            print(f"  {count:3d}x  {dtype}")
        print()

        print("--- 详细差异 ---")
        for sheet_name, diffs in results:
            print(f"\n{sheet_name}:")
            for d in diffs:
                print(f"  {d}")

    print()


def main():
    import argparse
    import sys

    parser = argparse.ArgumentParser(description='语义对照测试：生成 SQL vs 手写 SQL')
    parser.add_argument('excel_file', help='Excel 映射文件')
    parser.add_argument('hand_written_dir', help='手写 SQL 文件目录')
    parser.add_argument('--dict-from', dest='dict_from',
                        action='append', default=[],
                        help='CASE 字典目录（可多次指定）')
    parser.add_argument('--sheet', help='只对照指定 sheet（默认全部）')
    args = parser.parse_args()

    dict_dirs = args.dict_from or None
    total, pass_count, diff_count, results = run_comparison(
        args.excel_file, args.hand_written_dir, dict_dirs, args.sheet)

    print_report(args.excel_file, args.hand_written_dir, dict_dirs,
                 total, pass_count, diff_count, results)

    return 0 if diff_count == 0 else 1


if __name__ == '__main__':
    import sys
    sys.exit(main())
