"""Field resolver: convert FieldMapping to SQL expression
via rule pipeline."""

import re
from typing import Optional
from dataclasses import dataclass

from .models import FieldMapping, MappingSegment
from .case_dict import CaseDictExtractor
from . import text_cleaner


# ---------------------------------------------------------------------------
# Resolution result tracking
# ---------------------------------------------------------------------------

@dataclass
class ResolveResult:
    """Result of resolving a single field."""
    expr: str
    rule_name: str  # which rule produced this
    category: str   # summary stats key
    todo: str = ''  # TODO/REVIEW annotation


# ---------------------------------------------------------------------------
# Rule base class
# ---------------------------------------------------------------------------

class Rule:
    """Base class for field resolution rules."""
    name: str = ''

    def match(self, fm: FieldMapping, seg: MappingSegment) -> bool:
        raise NotImplementedError

    def resolve(self, fm: FieldMapping, seg: MappingSegment,
                ctx: 'RuleContext') -> ResolveResult:
        raise NotImplementedError


class RuleContext:
    """Shared context for rule resolution — alias lookup, case dict, notes."""

    def __init__(self, case_dict: Optional[CaseDictExtractor] = None):
        self.case_dict = case_dict
        self.notes: list[str] = []
        self.results: list[ResolveResult] = []

    def note(self, msg: str):
        self.notes.append("[注意] " + msg)

    def resolve_alias(self, fm: FieldMapping, seg: MappingSegment) -> str:
        src = fm.source_table
        if not src:
            return seg.source_tables[0].alias if seg.source_tables else 'T1'
        if re.match(r'^T\d+$', src):
            return src
        for t in seg.source_tables:
            if t.table_name == src:
                return t.alias
        for t in seg.source_tables:
            if src in t.table_name or t.table_name in src:
                return t.alias
        return seg.source_tables[0].alias if seg.source_tables else 'T1'

    def lookup_case_dict(self, fm: FieldMapping,
                         seg: MappingSegment
                         ) -> Optional[str]:
        if not self.case_dict:
            return None
        source_field = fm.source_field
        if not source_field:
            return None
        m = re.match(r'\w+\((\w+)\)', source_field)
        if m:
            source_field = m.group(1)
        source_field = re.sub(r'[（）]', '', source_field).strip()

        expr = self.case_dict.lookup(fm.target_en_name, source_field)
        if expr:
            alias = self.resolve_alias(fm, seg)
            result = re.sub(r'\bT\d+\.', f'{alias}.', expr)
            result = re.sub(r'\n\s*\n', '\n', result)
            self.note(
                f"字段 {fm.target_en_name}({fm.target_cn_name}) "
                f"使用字典中的 CASE 映射（源: {source_field}）"
            )
            return result
        return None


# ---------------------------------------------------------------------------
# Rule implementations (ordered by priority per v2-generation-rules §3.2)
# ---------------------------------------------------------------------------

class Rule1_CaseInRule(Rule):
    """Col10 已有 CASE WHEN → 直接使用"""
    name = 'rule1_case_in_col10'

    def match(self, fm, seg):
        if not fm.mapping_rule:
            return False
        rule = fm.mapping_rule.strip()
        return bool(re.match(r'CASE\b', rule, re.IGNORECASE))

    def resolve(self, fm, seg, ctx):
        rule = fm.mapping_rule
        rule = text_cleaner.clean_sql_text(rule)
        rule = text_cleaner.convert_oracle_syntax(rule)
        # IS 'val' → = 'val'
        rule = re.sub(r"\bIS\s+'", "= '", rule, flags=re.IGNORECASE)
        alias = ctx.resolve_alias(fm, seg)
        rule = text_cleaner.qualify_columns(rule, alias)
        if seg.alias_map:
            rule = text_cleaner.replace_aliases(rule, seg.alias_map)
        return ResolveResult(expr=rule, rule_name=self.name,
                             category='rule_convert')


class Rule2_FunctionExpr(Rule):
    """Col7 含函数表达式 → 识别并转换"""
    name = 'rule2_func_expr'

    _FUNC_RE = re.compile(
        r'^(?:\w+\.)?(NVL|IFNULL|COALESCE|CONCAT|SUBSTR|TRIM'
        r'|SUM|MAX|MIN|COUNT|AVG'
        r'|WM_CONCAT|GROUP_CONCAT'
        r'|CASE)\s*[\(]',
        re.IGNORECASE
    )

    def match(self, fm, seg):
        if not fm.source_field:
            return False
        return bool(self._FUNC_RE.match(fm.source_field.strip()))

    def resolve(self, fm, seg, ctx):
        expr = fm.source_field.strip()
        expr = text_cleaner.clean_sql_text(expr)
        expr = text_cleaner.convert_oracle_syntax(expr)
        if seg.alias_map:
            expr = text_cleaner.replace_aliases(expr, seg.alias_map)

        # If it starts with CASE, treat like Rule1
        if re.match(r'CASE\b', expr, re.IGNORECASE):
            alias = ctx.resolve_alias(fm, seg)
            expr = re.sub(r"\bIS\s+'", "= '", expr, flags=re.IGNORECASE)
            expr = text_cleaner.qualify_columns(expr, alias)
            return ResolveResult(expr=expr, rule_name=self.name,
                                 category='rule_convert')

        # ALIAS.FUNC(...) → FUNC(ALIAS....)
        m = re.match(
            r'^(\w+)\.(SUM|MAX|MIN|COUNT|AVG|WM_CONCAT'
            r'|GROUP_CONCAT)\s*\((.+)\)\s*$',
            expr, re.IGNORECASE | re.DOTALL
        )
        if m:
            alias_part, func_name, args = m.group(1), m.group(2), m.group(3)
            if '.' not in args:
                args = f'{alias_part}.{args}'
            func_upper = func_name.upper()
            if func_upper == 'WM_CONCAT':
                func_upper = 'GROUP_CONCAT'
            return ResolveResult(expr=f'{func_upper}({args})',
                                 rule_name=self.name, category='func_expr')

        # Direct aggregate: add alias prefix to bare column names
        alias = ctx.resolve_alias(fm, seg)
        m = re.match(
            r'^(SUM|MAX|MIN|COUNT|AVG|GROUP_CONCAT|WM_CONCAT)\s*\((.+)\)\s*$',
            expr, re.IGNORECASE | re.DOTALL
        )
        if m:
            func_name, args = m.group(1), m.group(2)
            func_upper = func_name.upper()
            if func_upper == 'WM_CONCAT':
                func_upper = 'GROUP_CONCAT'
            args = re.sub(
                r'\b([A-Z_]\w+)\b',
                lambda mm: (
                    mm.group(0)
                    if '.' in mm.group(0)
                    or mm.group(0).upper() in (
                        'SEPARATOR', 'DISTINCT', 'ASC', 'DESC')
                    else f'{alias}.{mm.group(0)}'
                ),
                args
            )
            return ResolveResult(expr=f'{func_upper}({args})',
                                 rule_name=self.name, category='func_expr')

        # NVL(...) → IFNULL(...)
        m = re.match(r'IFNULL\s*\((.+?),\s*(.+?)\)\s*$', expr,
                     re.IGNORECASE)
        if m:
            return ResolveResult(expr=f"IFNULL({m.group(1)}, {m.group(2)})",
                                 rule_name=self.name, category='func_expr')

        # COALESCE pass-through
        if expr.upper().startswith('COALESCE'):
            return ResolveResult(expr=expr, rule_name=self.name,
                                 category='func_expr')

        ctx.note(
            f"字段 {fm.target_en_name}({fm.target_cn_name}) "
            f"源字段包含函数表达式: '{expr}'"
        )
        return ResolveResult(expr=expr, rule_name=self.name,
                             category='func_expr')


class Rule3_MappingRule(Rule):
    """Col10 有映射规则（非 CASE）→ 转换"""
    name = 'rule3_mapping_rule'

    def match(self, fm, seg):
        if not fm.mapping_rule:
            return False
        rule = fm.mapping_rule.strip()
        if rule.startswith('__MULTI_SRC__'):
            return False
        if re.match(r'CASE\b', rule, re.IGNORECASE):
            return False  # handled by Rule1
        return True

    def resolve(self, fm, seg, ctx):
        rule = fm.mapping_rule.strip()
        rule = text_cleaner.clean_sql_text(rule)
        alias = ctx.resolve_alias(fm, seg)

        # "需转换" marker
        if rule in ('转换', '需转换'):
            dict_expr = ctx.lookup_case_dict(fm, seg)
            if dict_expr:
                return ResolveResult(expr=dict_expr, rule_name=self.name,
                                     category='dict_hit')
            ctx.note(
                f"字段 {fm.target_en_name}({fm.target_cn_name}) "
                f"标注'需转换'，源字典值未知，暂直取"
            )
            return ResolveResult(
                expr=f"{alias}.{fm.source_field}",
                rule_name=self.name, category='type_mismatch',
                todo="TODO: 标注'需转换'但无字典信息")

        rule = text_cleaner.convert_oracle_syntax(rule)

        # DATE_FORMAT from Oracle TO_CHAR conversion
        match = re.match(
            r"DATE_FORMAT\((\w+\.\w+)\s*,\s*'([^']+)'\)",
            rule, re.IGNORECASE)
        if match:
            field_ref = match.group(1)
            fmt = match.group(2)
            default = _get_date_default(fm)
            expr = f"DATE_FORMAT({field_ref}, '{fmt}')"
            if default:
                expr = f"COALESCE({expr}, '{default}')"
            return ResolveResult(expr=expr, rule_name=self.name,
                                 category='date_fmt')

        # IF ... THEN ... ELSE ... END IF → CASE WHEN
        match = re.match(
            r"IF\s+(.+?)\s+THEN\s*\n?\s*'(.+?)'"
            r"\s*\n?\s*ELSE\s*\n?\s*'(.+?)'"
            r"\s*\n?\s*END\s*IF",
            rule, re.IGNORECASE | re.DOTALL
        )
        if match:
            condition = match.group(1).strip()
            then_val = match.group(2)
            else_val = match.group(3)
            condition = re.sub(r'\bOr\b', 'OR', condition)
            condition = text_cleaner.qualify_columns(condition, alias)
            return ResolveResult(
                expr=(f"CASE WHEN {condition} "
                      f"THEN '{then_val}' "
                      f"ELSE '{else_val}' END"),
                rule_name=self.name, category='rule_convert')

        # NVL → IFNULL (already done by convert_oracle_syntax, but check)
        match = re.match(r"IFNULL\((.+?),\s*(.+?)\)", rule, re.IGNORECASE)
        if match:
            return ResolveResult(
                expr=f"IFNULL({match.group(1)}, {match.group(2)})",
                rule_name=self.name, category='rule_convert')

        # Chinese text description → TODO
        has_chinese = re.search(r'[\u4e00-\u9fff]', rule)
        has_sql_op = re.search(r'[=<>()]', rule)
        if has_chinese and not has_sql_op:
            ctx.note(
                f"字段 {fm.target_en_name}({fm.target_cn_name}) "
                f"映射规则为文字说明: '{rule}'"
            )
            return ResolveResult(
                expr=f"''  /* TODO: 映射规则\"{rule}\"，请补充 SQL 逻辑 */",
                rule_name=self.name, category='text_rule',
                todo=f'TODO: 映射规则"{rule}"')

        # Subquery → TODO
        if re.search(r'\bSELECT\b.*\bFROM\b', rule, re.IGNORECASE):
            ctx.note(
                f"字段 {fm.target_en_name}({fm.target_cn_name}) "
                f"映射规则含子查询: '{rule}'"
            )
            return ResolveResult(
                expr=f"''  /* TODO: 需补充子查询逻辑。原文: {rule} */",
                rule_name=self.name, category='text_rule',
                todo='TODO: 子查询')

        # Product category placeholder
        if '产品类别' in rule or '区分' in rule:
            ctx.note(
                f"字段 {fm.target_en_name}({fm.target_cn_name}) "
                f"映射规则 '{rule}' 需人工补充具体逻辑"
            )
            return ResolveResult(
                expr=f"'0'  /* TODO: 映射规则\"{rule}\"，请补充 SQL 逻辑 */",
                rule_name=self.name, category='text_rule',
                todo=f'TODO: 映射规则"{rule}"')

        # Unrecognized rule → pass through with TODO
        if seg.alias_map:
            rule = text_cleaner.replace_aliases(rule, seg.alias_map)
        rule = text_cleaner.qualify_columns(rule, alias)
        ctx.note(
            f"字段 {fm.target_en_name}({fm.target_cn_name}) "
            f"映射规则无法自动转换: '{rule}'"
        )
        return ResolveResult(
            expr=f"{rule}  /* REVIEW: 映射规则已直接使用，请确认 */",
            rule_name=self.name, category='rule_convert',
            todo='REVIEW: 映射规则直接使用')


class Rule4_DateConversion(Rule):
    """Col9=DATE 且 Col3=VARCHAR → 日期格式转换"""
    name = 'rule4_date_conversion'

    def match(self, fm, seg):
        return _is_date_conversion(fm)

    def resolve(self, fm, seg, ctx):
        alias = ctx.resolve_alias(fm, seg)
        default = _get_date_default(fm)
        expr = f"DATE_FORMAT({alias}.{fm.source_field}, '%Y-%m-%d')"
        if default:
            expr = f"COALESCE({expr}, '{default}')"
        return ResolveResult(expr=expr, rule_name=self.name,
                             category='date_fmt')


class Rule5_YNFlag(Rule):
    """_FLAG 标志位 Y/N → 0/1"""
    name = 'rule5_yn_flag'

    def match(self, fm, seg):
        return _is_yn_flag(fm)

    def resolve(self, fm, seg, ctx):
        alias = ctx.resolve_alias(fm, seg)
        expr = (f"CASE WHEN {alias}.{fm.source_field} = 'Y' "
                f"THEN '1' ELSE '0' END")
        return ResolveResult(expr=expr, rule_name=self.name,
                             category='flag')


class Rule6_DictMismatch(Rule):
    """码值字典不匹配 → 三级递进 CASE 策略"""
    name = 'rule6_dict_mismatch'

    def match(self, fm, seg):
        if not fm.source_field:
            return False
        # Check for type width mismatch or explicit dict info
        src_type = fm.source_type.upper() if fm.source_type else ''
        tgt_type = fm.target_type.upper() if fm.target_type else ''
        src_w = re.search(r'VARCHAR\w*\((\d+)\)', src_type)
        tgt_w = re.search(r'VARCHAR\w*\((\d+)\)', tgt_type)
        if src_w and tgt_w:
            sw, tw = int(src_w.group(1)), int(tgt_w.group(1))
            if sw > tw and tw <= 4:
                return True

        # INT → small VARCHAR
        if ('INTEGER' in src_type or 'INT' in src_type) and tgt_w:
            if int(tgt_w.group(1)) <= 2:
                return True

        # fill_instruction says "需转换"
        if fm.fill_instruction and fm.fill_instruction.strip() in ('转换', '需转换'):
            return True

        return False

    def resolve(self, fm, seg, ctx):
        alias = ctx.resolve_alias(fm, seg)

        # Level 1: dict-from lookup
        dict_expr = ctx.lookup_case_dict(fm, seg)
        if dict_expr:
            return ResolveResult(expr=dict_expr, rule_name=self.name,
                                 category='dict_hit')

        # Level 2: Col4/Col13 skeleton
        target_codes = _extract_target_codes(fm)
        if target_codes:
            lines = [f"CASE {alias}.{fm.source_field}"]
            for code, desc in target_codes:
                lines.append(f"    WHEN '' THEN '{code}'  -- {desc}")
            lines.append("    ELSE ''")
            lines.append(
                f"END  /* TODO: 请根据源系统字典填写 WHEN 条件值。"
                f"目标字段: {fm.target_en_name}, 源字段: {fm.source_field} */")
            expr = '\n'.join(lines)
            ctx.note(
                f"字段 {fm.target_en_name}({fm.target_cn_name}) "
                f"生成了 CASE 骨架（{len(target_codes)} 个码值），需填写源码值"
            )
            return ResolveResult(
                expr=expr, rule_name=self.name,
                category='case_skeleton',
                todo='TODO: 请根据源系统字典填写 WHEN 条件值')

        # Level 3: direct + TODO warning
        src_type = fm.source_type or '?'
        tgt_type = fm.target_type or '?'
        ctx.note(
            f"字段 {fm.target_en_name}({fm.target_cn_name}) "
            f"源类型 {src_type} → 目标类型 {tgt_type}，"
            f"可能需要 CASE 映射"
        )
        return ResolveResult(
            expr=(f"{alias}.{fm.source_field}  "
                  f"/* TODO: 源/目标类型宽度不匹配 ({src_type}→{tgt_type})，"
                  f"可能需要 CASE 映射 */"),
            rule_name=self.name, category='type_mismatch',
            todo=f'TODO: 源/目标类型宽度不匹配 ({src_type}→{tgt_type})')


class Rule7_DirectMapping(Rule):
    """直取字段"""
    name = 'rule7_direct'

    def match(self, fm, seg):
        return bool(fm.source_field)

    def resolve(self, fm, seg, ctx):
        alias = ctx.resolve_alias(fm, seg)

        # Multi-source marker
        if fm.mapping_rule and fm.mapping_rule.startswith('__MULTI_SRC__'):
            extra_field = fm.mapping_rule.split(':')[1]
            expr = (f"CASE WHEN {alias}.{fm.source_field} > 0 "
                    f"OR {alias}.{extra_field} > 0 THEN '1' ELSE '0' END")
            return ResolveResult(expr=expr, rule_name=self.name,
                                 category='multi_source')

        # Conditional fill from fill_instruction
        if fm.fill_instruction and '当' in fm.fill_instruction:
            cond_expr = _gen_conditional_fill(fm, seg, ctx)
            if cond_expr:
                return ResolveResult(
                    expr=cond_expr, rule_name=self.name,
                    category='conditional',
                    todo='REVIEW: 填报说明含条件，已尝试生成 CASE，请确认')

        # Multi-table source warning
        if ',' in fm.source_table:
            ctx.note(
                f"字段 {fm.target_en_name}({fm.target_cn_name}) "
                f"引用多个源表 '{fm.source_table}'，需人工补充取值逻辑"
            )
            return ResolveResult(
                expr=(f"{alias}.{fm.source_field}  "
                      f"/* TODO: 源表名含多表 ({fm.source_table})，"
                      f"需补充取值逻辑 */"),
                rule_name=self.name, category='multi_source',
                todo=f'TODO: 源表名含多表 ({fm.source_table})')

        # Chinese source field name → not a valid column
        if re.search(r'[\u4e00-\u9fff]', fm.source_field):
            ctx.note(
                f"字段 {fm.target_en_name}({fm.target_cn_name})"
                f" 源字段为中文 '{fm.source_field}'，"
                f"不是合法 SQL 列名，输出空字符串"
            )
            return ResolveResult(expr="''", rule_name=self.name,
                                 category='empty')

        return ResolveResult(expr=f"{alias}.{fm.source_field}",
                             rule_name=self.name, category='direct')


class Rule8_CollectDate(Rule):
    """采集日期字段 → V_DATE"""
    name = 'rule8_collect_date'

    def match(self, fm, seg):
        if fm.source_field == 'V_DATE':
            return True
        if not fm.source_field and not fm.source_table:
            if '采集日期' in fm.target_cn_name or '采集' in fm.target_cn_name:
                return True
        return False

    def resolve(self, fm, seg, ctx):
        return ResolveResult(expr='V_DATE', rule_name=self.name,
                             category='vdate')


class Rule9_EmptyConstant(Rule):
    """空值/常量"""
    name = 'rule9_empty'

    def match(self, fm, seg):
        return True  # fallback rule, always matches

    def resolve(self, fm, seg, ctx):
        if fm.source_table and not fm.source_field:
            ctx.note(
                f"字段 {fm.target_en_name}({fm.target_cn_name}) "
                f"源表为 '{fm.source_table}' 但源字段为空，输出空字符串"
            )
        return ResolveResult(expr="''", rule_name=self.name,
                             category='empty')


# ---------------------------------------------------------------------------
# Rule pipeline
# ---------------------------------------------------------------------------

# Ordered by priority -- first match wins
_RULE_PIPELINE: list[Rule] = [
    Rule8_CollectDate(),    # V_DATE / collect date
    Rule1_CaseInRule(),     # CASE in Col10
    Rule2_FunctionExpr(),   # function in Col7
    Rule3_MappingRule(),    # mapping rule (non-CASE)
    Rule4_DateConversion(),  # DATE -> VARCHAR
    Rule5_YNFlag(),         # _FLAG Y/N -> 0/1
    Rule6_DictMismatch(),   # dict mismatch -> CASE
    Rule7_DirectMapping(),  # direct field mapping
    Rule9_EmptyConstant(),  # fallback empty
]


# ---------------------------------------------------------------------------
# Main resolver class
# ---------------------------------------------------------------------------

class FieldResolver:
    """Resolve FieldMapping into SQL expression via rule pipeline."""

    def __init__(self, case_dict: Optional[CaseDictExtractor] = None):
        self.ctx = RuleContext(case_dict=case_dict)

    @property
    def notes(self) -> list[str]:
        return self.ctx.notes

    @property
    def results(self) -> list[ResolveResult]:
        return self.ctx.results

    def resolve(self, fm: FieldMapping, seg: MappingSegment) -> str:
        """Generate a SELECT expression for a single field mapping."""
        for rule in _RULE_PIPELINE:
            if rule.match(fm, seg):
                result = rule.resolve(fm, seg, self.ctx)
                self.ctx.results.append(result)
                return result.expr

        # Should never reach here (Rule9 always matches)
        return "''"

    def get_summary_stats(self) -> dict[str, int]:
        """Return category → count mapping for generation summary."""
        stats: dict[str, int] = {}
        for r in self.ctx.results:
            stats[r.category] = stats.get(r.category, 0) + 1
        return stats

    def get_todo_count(self) -> int:
        """Count fields that have TODO annotations."""
        return sum(1 for r in self.ctx.results if r.todo.startswith('TODO'))

    def get_review_count(self) -> int:
        """Count fields that have REVIEW annotations."""
        return sum(1 for r in self.ctx.results if r.todo.startswith('REVIEW'))


# ---------------------------------------------------------------------------
# Helper functions (shared by multiple rules)
# ---------------------------------------------------------------------------

def _is_date_conversion(fm: FieldMapping) -> bool:
    src_type = fm.source_type.upper() if fm.source_type else ''
    tgt_type = fm.target_type.upper() if fm.target_type else ''
    return ('DATE' in src_type and 'DATE' not in tgt_type
            and 'VARCHAR' in tgt_type)


def _get_date_default(fm: FieldMapping) -> str:
    desc = ((fm.fill_instruction or '') +
            (fm.description or '') +
            (fm.target_cn_name or ''))
    if '9999-12-31' in desc:
        return '9999-12-31'
    if '9999-12' in desc:
        return '9999-12'
    return ''


def _is_yn_flag(fm: FieldMapping) -> bool:
    if not fm.source_field:
        return False
    if '_FLAG' not in fm.source_field.upper():
        return False
    td = fm.target_dict or ''
    if re.search(r'[01].*[否是]|[是否].*[01]|0[-.:]否|1[-.:]是', td):
        return True
    if '0' in td and '1' in td:
        return True
    tgt_type = (fm.target_type or '').upper()
    m = re.search(r'VARCHAR\w*\((\d+)\)', tgt_type)
    if m and int(m.group(1)) <= 2 and not td:
        return True
    return False


def _extract_target_codes(fm: FieldMapping) -> list[tuple[str, str]]:
    """Extract target code values from Col4 (target_dict) or Col13 (description).

    Returns list of (code, description) tuples.
    """
    codes = []

    # Try Col4 first
    text = fm.target_dict or ''
    if text:
        codes = _parse_code_list(text)

    # Try Col13 if Col4 didn't yield codes
    if not codes and fm.description:
        codes = _parse_code_list(fm.description)

    return codes


def _parse_code_list(text: str) -> list[tuple[str, str]]:
    """Parse code list from text like '01 央行\\n02 政策性银行' or '0.否,1.是'."""
    codes = []

    # Pattern 1: "01 描述" per line
    for m in re.finditer(r'(\d{1,4})\s+(\S+)', text):
        code, desc = m.group(1), m.group(2)
        # Skip if desc looks like another code or header
        if desc in ('代码', '名称', '说明', '字段'):
            continue
        codes.append((code, desc))

    if codes:
        return codes

    # Pattern 2: "0.否,1.是" or "0.否 1.是"
    for m in re.finditer(r'(\d+)[.．](\S+?)(?=[,，\s]|$)', text):
        codes.append((m.group(1), m.group(2)))

    return codes


def _gen_conditional_fill(fm: FieldMapping, seg: MappingSegment,
                          ctx: RuleContext) -> str:
    """Generate CASE WHEN from fill_instruction '当...时填...'."""
    inst = fm.fill_instruction
    alias = ctx.resolve_alias(fm, seg)

    m = re.search(r'当.*?为(.+?)时', inst)
    if not m:
        return ''

    condition_text = m.group(1).strip()

    cond_field = ''
    if '渠道' in inst:
        cond_field = f'{alias}.CHANNEL_TYPE_CODE'

    if not cond_field:
        ctx.note(
            f"字段 {fm.target_en_name}({fm.target_cn_name}) "
            f"填报说明有条件逻辑但无法自动识别条件字段: '{inst}'"
        )
        return ''

    channel_map = {
        '柜面': '01', 'ATM': '02', 'ATM机': '02',
        '自助终端': '02', 'VTM': '03', 'POS': '04',
        '网银': '05', '手机银行': '06', '手机': '06',
        '第三方支付': '07', '银联': '08',
    }
    parts = re.split(r'[\\\\、/]', condition_text)
    seen = set()
    channel_codes = []
    for p in parts:
        p = p.strip()
        if p in channel_map and channel_map[p] not in seen:
            channel_codes.append(channel_map[p])
            seen.add(channel_map[p])

    if not channel_codes:
        ctx.note(
            f"字段 {fm.target_en_name}({fm.target_cn_name}) "
            f"无法解析条件值: '{condition_text}'"
        )
        return ''

    if len(channel_codes) == 1:
        cond = f"{cond_field} = '{channel_codes[0]}'"
    else:
        in_list = ', '.join(f"'{c}'" for c in channel_codes)
        cond = f"{cond_field} IN ({in_list})"

    return (f"CASE WHEN {cond} "
            f"THEN {alias}.{fm.source_field} ELSE '' END")
