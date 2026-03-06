"""SQL writer: assemble stored procedure from SheetMapping."""

import re
from typing import Optional

from .models import SheetMapping, MappingSegment
from .field_resolver import FieldResolver
from .case_dict import CaseDictExtractor
from .config import GeneratorConfig


# Aggregate function pattern
_AGG_FUNCS_RE = re.compile(
    r'\b(SUM|MAX|MIN|COUNT|AVG|GROUP_CONCAT)\s*\(',
    re.IGNORECASE
)


class SQLWriter:
    """Generate a complete MySQL stored procedure."""

    def __init__(self, mapping: SheetMapping,
                 case_dict: Optional[CaseDictExtractor] = None,
                 config: Optional[GeneratorConfig] = None):
        self.mapping = mapping
        self.config = config or GeneratorConfig()
        self.resolver = FieldResolver(case_dict=case_dict)

    @property
    def notes(self) -> list[str]:
        return self.resolver.notes

    def generate(self) -> str:
        m = self.mapping
        date_field = self._find_date_field(m.segments[0])

        lines = []
        lines.append(self._gen_header(m, date_field))

        for i, seg in enumerate(m.segments, 1):
            lines.append(self._gen_segment(
                seg, i, len(m.segments)))

        lines.append(self._gen_footer())
        lines.append(self._gen_summary(m))

        return '\n'.join(lines)

    def _find_date_field(self, segment: MappingSegment) -> str:
        for fm in reversed(segment.field_mappings):
            cn = fm.target_cn_name
            if '采集日期' in cn or '采集' in cn:
                return fm.target_en_name
            if (fm.source_field == 'V_DATE'
                    or fm.mapping_rule == 'V_DATE'):
                return fm.target_en_name
        if segment.field_mappings:
            return segment.field_mappings[-1].target_en_name
        return 'DATE_FIELD'

    def _table_ref(self, table_name: str) -> str:
        """Add schema prefix to table name if configured."""
        schema = self.config.target_schema
        if schema:
            return f"{schema}.{table_name}"
        return table_name

    def _source_ref(self, table_name: str) -> str:
        """Add source schema prefix to table name."""
        schema = self.config.source_schema
        if schema:
            return f"{schema}.{table_name}"
        return table_name

    def _gen_header(self, m: SheetMapping,
                    date_field: str) -> str:
        all_tables = []
        for seg in m.segments:
            for t in seg.source_tables:
                info = (f"--           "
                        f"{t.table_name} "
                        f"({t.alias} {t.table_cn_name})")
                if info not in all_tables:
                    all_tables.append(info)

        tables_comment = '\n'.join(all_tables)
        if all_tables:
            tables_comment = (
                all_tables[0].replace(
                    '--           ', '-- 源    表: ')
                + '\n' + '\n'.join(all_tables[1:]))

        cfg = self.config
        table_ref = self._table_ref(m.target_table)

        # Proc name
        proc_name = f"Pids_{m.target_table.lower()}"

        # DEFINER clause
        definer = ''
        if cfg.definer:
            definer = f"DEFINER={cfg.definer} "

        # Schema prefix for proc
        proc_prefix = ''
        if cfg.proc_schema:
            proc_prefix = f"`{cfg.proc_schema}`."

        return f"""\
CREATE {definer}PROCEDURE {proc_prefix}{proc_name}(
    IN I_DATE VARCHAR(8),   -- 数据日期，格式 YYYYMMDD
    OUT O_RLT VARCHAR(10)   -- 返回结果
)
BEGIN
    -- ---------------------------------------------------------
    -- 功能描述: {m.target_cn_name} ({m.target_table})
    -- 传入参数: I_DATE 格式 YYYYMMDD
    {tables_comment}
    -- 目 标 表: {m.target_table}
    -- ---------------------------------------------------------

    DECLARE V_DATE VARCHAR(8);
    DECLARE V_START_DT DATETIME;
    DECLARE V_PRD_NAME VARCHAR(100);
    DECLARE V_TAB_NAME VARCHAR(100);
    DECLARE V_TOTAL_NUM INT DEFAULT 0;
    DECLARE V_TAGS VARCHAR(300);
    DECLARE V_MSG TEXT;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        GET DIAGNOSTICS CONDITION 1 V_MSG = MESSAGE_TEXT;
        SET O_RLT = 'false';
        SET V_TAGS = CONCAT('第', V_TAGS, '段报错：', SUBSTRING(V_MSG, 1, 200));
        CALL {cfg.log_call}(V_DATE, V_PRD_NAME, V_TAB_NAME, V_TAGS,
                          O_RLT, V_TOTAL_NUM, V_START_DT, NOW());
    END;

    -- 初始化变量
    SET V_DATE = {cfg.vdate_expr};
    SET V_START_DT = NOW();
    SET V_PRD_NAME = '{proc_name}';
    SET V_TAB_NAME = '{m.target_table}';
    SET V_TOTAL_NUM = 0;

    -- 删除当期数据（按采集日期）
    DELETE FROM {table_ref} WHERE {date_field} = V_DATE;
"""

    def _gen_segment(self, seg: MappingSegment,
                     seg_idx: int, total_segs: int) -> str:
        lines = []
        seg_label = (seg.segment_name
                     if seg.segment_name != '默认段'
                     else f'第{seg_idx}段')

        lines.append(
            f"    -- ======================== "
            f"{seg_label} BEGIN "
            f"========================")
        lines.append(f"    SET V_TAGS = '{seg_idx}';")
        lines.append("")

        # INSERT column list
        valid_fields = [
            fm for fm in seg.field_mappings
            if fm.target_en_name
        ]
        table_ref = self._table_ref(self.mapping.target_table)

        lines.append(f"    INSERT INTO {table_ref} (")
        for i, fm in enumerate(valid_fields):
            comma = ',' if i < len(valid_fields) - 1 else ''
            lines.append(
                f"        {fm.target_en_name}{comma}"
                f"   -- {fm.target_cn_name}")
        lines.append("    )")

        # SELECT expressions
        lines.append("    SELECT")
        select_exprs = []
        has_aggregate = False
        for fm in valid_fields:
            expr = self.resolver.resolve(fm, seg)
            select_exprs.append(expr)
            if _AGG_FUNCS_RE.search(expr):
                has_aggregate = True

        for i, (fm, expr) in enumerate(
                zip(valid_fields, select_exprs)):
            comma = ',' if i < len(valid_fields) - 1 else ''
            lines.append(
                f"        {expr}{comma}"
                f"   -- {fm.target_cn_name}")

        # FROM / JOIN
        lines.append("")
        main_table = seg.source_tables[0]
        main_ref = self._source_ref(main_table.table_name)
        lines.append(
            f"    FROM {main_ref} {main_table.alias}")
        for t in seg.source_tables[1:]:
            if not t.join_type:
                continue
            t_ref = self._source_ref(t.table_name)
            lines.append(
                f"    {t.join_type} {t_ref} {t.alias}")
            lines.append(
                f"        ON {t.join_condition}")

        # WHERE
        if seg.where_conditions:
            for j, wc in enumerate(seg.where_conditions):
                prefix = "WHERE" if j == 0 else "  AND"
                lines.append(
                    f"    {prefix} {wc.condition}")

        # GROUP BY
        if has_aggregate:
            group_cols = []
            for fm, expr in zip(valid_fields, select_exprs):
                if not _AGG_FUNCS_RE.search(expr):
                    stripped = expr.strip()
                    if (stripped.startswith("'")
                            or stripped == 'V_DATE'
                            or stripped.replace('.', ''
                                                ).isdigit()):
                        continue
                    group_cols.append(expr)
            if group_cols:
                lines.append("    GROUP BY")
                for i, col in enumerate(group_cols):
                    comma = (',' if i < len(group_cols) - 1
                             else '')
                    lines.append(
                        f"        {col}{comma}")

        lines.append("    ;")
        lines.append("")
        lines.append(
            "    SET V_TOTAL_NUM = V_TOTAL_NUM + ROW_COUNT();")
        lines.append("    COMMIT;")
        lines.append(
            f"    -- ======================== "
            f"{seg_label} END "
            f"==========================")
        lines.append("")

        return '\n'.join(lines)

    def _gen_footer(self) -> str:
        log_call = self.config.log_call
        return f"""\
    SET O_RLT = 'true';

    -- 记录日志
    CALL {log_call}(V_DATE, V_PRD_NAME, V_TAB_NAME, V_TAGS,
                      O_RLT, V_TOTAL_NUM, V_START_DT, NOW());

END;
"""

    def _gen_summary(self, m: SheetMapping) -> str:
        """Generate summary block at end of procedure."""
        stats = self.resolver.get_summary_stats()
        total = sum(stats.values())
        if total == 0:
            return ''

        # Auto-mapped categories
        auto_cats = {
            'direct': '直取字段',
            'date_fmt': '日期转换',
            'flag': 'FLAG转换',
            'dict_hit': 'dict-from命中',
            'rule_convert': 'Col10规则转换',
            'func_expr': '函数表达式',
            'vdate': 'V_DATE',
            'conditional': '条件映射',
        }
        # Manual categories
        manual_cats = {
            'case_skeleton': '码值CASE骨架(WHEN留空)',
            'type_mismatch': '类型不匹配警告',
            'text_rule': '映射规则文字说明',
            'multi_source': '多源表取值',
        }

        auto_count = sum(
            stats.get(k, 0) for k in auto_cats)
        manual_count = sum(
            stats.get(k, 0) for k in manual_cats)
        empty_count = stats.get('empty', 0)

        lines = []
        lines.append(
            "/* =========================================="
            "======================")
        lines.append("   自动生成摘要")
        lines.append(
            "   ----------------------------------------"
            "----------------------")
        lines.append(
            f"   目标表: {m.target_table} "
            f"({m.target_cn_name})")
        lines.append(
            f"   段数: {len(m.segments)}")
        lines.append(
            f"   总字段: {total}")
        lines.append(
            "   ----------------------------------------"
            "----------------------")

        if total > 0:
            auto_pct = auto_count * 100 // total
            lines.append(
                f"   自动映射: {auto_count} 个字段"
                f" ({auto_pct}%)")
            for cat, label in auto_cats.items():
                n = stats.get(cat, 0)
                if n > 0:
                    lines.append(
                        f"     - {label}: {n}")

            if empty_count > 0:
                lines.append(
                    f"   空值/常量: {empty_count} 个字段")

            if manual_count > 0:
                manual_pct = manual_count * 100 // total
                lines.append(
                    f"   需人工确认: {manual_count} 个字段"
                    f" ({manual_pct}%)")
                search_hints = {
                    'case_skeleton': '搜索 "TODO: 请根据源系统字典"',
                    'type_mismatch': '搜索 "TODO: 源/目标类型"',
                    'text_rule': '搜索 "TODO: 映射规则"',
                    'multi_source': '搜索 "TODO: 源表名含多表"',
                }
                for cat, label in manual_cats.items():
                    n = stats.get(cat, 0)
                    if n > 0:
                        hint = search_hints.get(cat, '')
                        suffix = (f'  -> {hint}'
                                  if hint else '')
                        lines.append(
                            f"     - {label}: {n}"
                            f"{suffix}")

        lines.append(
            "   =========================================="
            "====================== */")
        return '\n'.join(lines)
