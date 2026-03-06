#!/usr/bin/env python3
"""
语义对照测试：比较 generate_sql.py 生成的 SQL 与手写 SQL 的结构差异。

用法:
    python compare_sql.py <excel_file> <hand_written_dir> [--dict-from DIR] [--sheet SHEET]

比较维度:
    1. 段数 (INSERT...SELECT 块数)
    2. 每段: INSERT 列数 vs SELECT 表达式数
    3. FROM 源表名
    4. SELECT 中 CASE 表达式的位置和源字段
    5. WHERE 条件数量
    6. GROUP BY 有无
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field


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
            # skip to end of line
            j = text.find('\n', i)
            if j < 0:
                break
            i = j  # keep the \n
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
    # 先去除行内注释
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


def _strip_inline_comment(col: str) -> str:
    """去除列名中的 -- 注释部分，如 'A010007   -- 金融机构类型代码' → 'A010007'。"""
    idx = col.find('--')
    if idx >= 0:
        col = col[:idx]
    return col.strip()


def parse_sql_structure(sql_text: str) -> list[SegmentInfo]:
    """解析 SQL 文本，提取每个 INSERT...SELECT 段的结构信息。"""
    segments = []

    for m in re.finditer(
        r'insert\s+into\s+[\w.]+\s*\(([^)]+)\)\s*\n?\s*select\s+(.+?)\n\s*from\s+(\S+)',
        sql_text, re.DOTALL | re.IGNORECASE,
    ):
        seg = SegmentInfo()
        # 先去除注释，再按逗号拆分列名
        clean_insert = strip_comments(m.group(1))
        seg.insert_cols = [
            c.strip() for c in clean_insert.split(',')
            if c.strip()
        ]

        select_text = m.group(2)
        seg.select_exprs = parse_select_exprs(select_text)

        seg.from_tables = [m.group(3)]

        # Extract remaining text after FROM for JOINs, WHERE, GROUP BY
        rest_start = m.end()
        # Find the end of this segment (next INSERT or end of file)
        next_insert = re.search(r'\binsert\s+into\b', sql_text[rest_start:], re.IGNORECASE)
        rest_end = rest_start + next_insert.start() if next_insert else len(sql_text)
        rest = sql_text[rest_start:rest_end]

        # JOINs
        for jm in re.finditer(r'(LEFT|RIGHT|INNER|CROSS)?\s*JOIN\s+(\S+)', rest, re.IGNORECASE):
            seg.joins.append(jm.group(2))
            seg.from_tables.append(jm.group(2))

        # WHERE count
        seg.where_count = len(re.findall(r'\bWHERE\b|\bAND\b', rest, re.IGNORECASE))

        # GROUP BY
        seg.has_group_by = bool(re.search(r'\bGROUP\s+BY\b', rest, re.IGNORECASE))

        # CASE fields
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

        # INSERT columns count
        if len(g.insert_cols) != len(r.insert_cols):
            diffs.append(
                f"{prefix} INSERT列数: 生成={len(g.insert_cols)}, 手写={len(r.insert_cols)}"
            )

        # SELECT expressions count
        if len(g.select_exprs) != len(r.select_exprs):
            diffs.append(
                f"{prefix} SELECT表达式数: 生成={len(g.select_exprs)}, 手写={len(r.select_exprs)}"
            )

        # INSERT == SELECT balance
        if len(g.insert_cols) != len(g.select_exprs):
            diffs.append(
                f"{prefix} 生成SQL列数不平衡: INSERT={len(g.insert_cols)}, SELECT={len(g.select_exprs)}"
            )
        if len(r.insert_cols) != len(r.select_exprs):
            diffs.append(
                f"{prefix} 手写SQL列数不平衡: INSERT={len(r.insert_cols)}, SELECT={len(r.select_exprs)}"
            )

        # FROM tables (ignore schema prefix)
        g_tables = {t.split('.')[-1].upper() for t in g.from_tables}
        r_tables = {t.split('.')[-1].upper() for t in r.from_tables}
        if g_tables != r_tables:
            only_gen = g_tables - r_tables
            only_ref = r_tables - g_tables
            if only_gen:
                diffs.append(f"{prefix} 仅生成有的表: {only_gen}")
            if only_ref:
                diffs.append(f"{prefix} 仅手写有的表: {only_ref}")

        # GROUP BY
        if g.has_group_by != r.has_group_by:
            diffs.append(
                f"{prefix} GROUP BY: 生成={'有' if g.has_group_by else '无'}, "
                f"手写={'有' if r.has_group_by else '无'}"
            )

        # CASE expressions comparison
        g_case_cols = {col for _, col, _ in g.case_fields}
        r_case_cols = {col for _, col, _ in r.case_fields}

        # CASE in ref but not in gen (we're missing a CASE)
        missing_case = r_case_cols - g_case_cols
        if missing_case:
            # Find source fields for missing CASEs
            details = []
            for _, col, src in r.case_fields:
                if col in missing_case:
                    details.append(f"{col}({src})")
            diffs.append(
                f"{prefix} 缺少CASE映射(手写有,生成无): {', '.join(details)}"
            )

        # CASE in gen but not in ref (extra CASE)
        extra_case = g_case_cols - r_case_cols
        if extra_case:
            details = []
            for _, col, src in g.case_fields:
                if col in extra_case:
                    details.append(f"{col}({src})")
            diffs.append(
                f"{prefix} 多出CASE映射(生成有,手写无): {', '.join(details)}"
            )

        # For shared CASE fields, compare source fields
        shared = g_case_cols & r_case_cols
        for col in sorted(shared):
            g_src = next(src for _, c, src in g.case_fields if c == col)
            r_src = next(src for _, c, src in r.case_fields if c == col)
            if g_src.upper() != r_src.upper():
                diffs.append(
                    f"{prefix} CASE源字段不同 {col}: 生成={g_src}, 手写={r_src}"
                )

    return diffs


def main():
    parser = argparse.ArgumentParser(description='语义对照测试：生成 SQL vs 手写 SQL')
    parser.add_argument('excel_file', help='Excel 映射文件')
    parser.add_argument('hand_written_dir', help='手写 SQL 文件目录')
    parser.add_argument('--dict-from', dest='dict_from', help='CASE 字典目录')
    parser.add_argument('--sheet', help='只对照指定 sheet（默认全部）')
    args = parser.parse_args()

    import openpyxl
    wb = openpyxl.load_workbook(args.excel_file, data_only=True)
    sheets = [s for s in wb.sheetnames if s != '目录']
    if args.sheet:
        sheets = [s for s in sheets if args.sheet in s]

    sql_dir = args.hand_written_dir

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
        # 支持多种命名: 1.1.txt, PROC_T_1_1.sql, T_1_1.sql
        candidates = [
            os.path.join(sql_dir, f'{num}.txt'),
            os.path.join(sql_dir, f'PROC_T_{major}_{minor}.sql'),
            os.path.join(sql_dir, f'T_{major}_{minor}.sql'),
        ]
        ref_file = next((f for f in candidates if os.path.exists(f)), None)
        if not ref_file:
            continue

        total += 1

        # Generate SQL
        with tempfile.NamedTemporaryFile(suffix='.sql', delete=False, mode='w') as tmp:
            tmp_path = tmp.name

        cmd = ['python3', 'generate_sql.py', args.excel_file, sheet_name, '-o', tmp_path]
        if args.dict_from:
            cmd += ['--dict-from', args.dict_from]

        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            results.append((sheet_name, ['生成失败']))
            diff_count += 1
            os.unlink(tmp_path)
            continue

        # Read both files
        with open(tmp_path, encoding='utf-8') as f:
            gen_sql = f.read()
        with open(ref_file, encoding='utf-8') as f:
            ref_sql = f.read()
        os.unlink(tmp_path)

        # Parse structure
        gen_segs = parse_sql_structure(gen_sql)
        ref_segs = parse_sql_structure(ref_sql)

        # Compare
        diffs = compare_segments(gen_segs, ref_segs, sheet_name)

        if diffs:
            diff_count += 1
            results.append((sheet_name, diffs))
        else:
            pass_count += 1

    # Output report
    print(f"{'='*70}")
    print(f"语义对照测试报告")
    print(f"Excel: {args.excel_file}")
    print(f"手写SQL: {sql_dir}")
    print(f"字典: {args.dict_from or '无'}")
    print(f"{'='*70}")
    print(f"总计: {total} 个 sheet, 通过: {pass_count}, 有差异: {diff_count}")
    print()

    if results:
        # Group by diff type for summary
        diff_types: dict[str, int] = {}
        for sheet_name, diffs in results:
            for d in diffs:
                # Extract diff type (first word/phrase before colon)
                dtype = d.split(':')[0].strip()
                # Simplify: remove segment prefix
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
    return 0 if diff_count == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
