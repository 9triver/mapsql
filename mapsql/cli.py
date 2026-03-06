"""Command-line interface for MapSQL."""

import argparse
import sys

from .excel_parser import ExcelParser
from .case_dict import CaseDictExtractor
from .sql_writer import SQLWriter


def main():
    parser = argparse.ArgumentParser(
        description='根据 Excel 映射定义生成 MySQL 存储过程 SQL')
    parser.add_argument('excel_file', help='Excel 文件路径')
    parser.add_argument('sheet_name', help='Sheet 名称')
    parser.add_argument('-o', '--output',
                        help='输出 SQL 文件路径（默认输出到标准输出）')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='显示详细信息')
    parser.add_argument('--dict-from', dest='dict_from', metavar='DIR',
                        action='append', default=[],
                        help='从指定目录的手写 SQL 文件中提取 CASE 映射字典（可多次指定）')
    args = parser.parse_args()

    # Load CASE dictionary
    case_dict = None
    if args.dict_from:
        case_dict = CaseDictExtractor()
        total = 0
        for d in args.dict_from:
            n = case_dict.load_from_directory(d)
            total += n
            print(f"[信息] 从 {d} 加载了 {n} 个 CASE 映射", file=sys.stderr)
        if len(args.dict_from) > 1:
            print(f"[信息] 合计 {total} 个 CASE 映射", file=sys.stderr)

    # Parse Excel
    ep = ExcelParser(args.excel_file, args.sheet_name)
    mapping = ep.parse()

    for w in ep.warnings:
        print(w, file=sys.stderr)
    for e in ep.errors:
        print(e, file=sys.stderr)

    if not mapping:
        print("\n解析失败，请检查以上错误信息。", file=sys.stderr)
        sys.exit(1)

    if args.verbose:
        print(f"\n目标表: {mapping.target_table} ({mapping.target_cn_name})",
              file=sys.stderr)
        for seg in mapping.segments:
            print(f"  段: {seg.segment_name}", file=sys.stderr)
            print(f"    源表: {[t.table_name + ' ' + t.alias for t in seg.source_tables]}",
                  file=sys.stderr)
            print(f"    条件: {len(seg.where_conditions)} 个", file=sys.stderr)
            print(f"    字段: {len(seg.field_mappings)} 个", file=sys.stderr)

    # Generate SQL
    gen = SQLWriter(mapping, case_dict=case_dict)
    sql = gen.generate()

    for n in gen.notes:
        print(n, file=sys.stderr)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(sql)
        print(f"\nSQL 已写入: {args.output}", file=sys.stderr)
    else:
        print(sql)


if __name__ == '__main__':
    main()
