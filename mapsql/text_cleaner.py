"""Text cleaning: fullwidth→halfwidth, Oracle→MySQL syntax, alias replacement."""

import re


# Fullwidth → halfwidth mapping
_FULLWIDTH_MAP = str.maketrans(
    '（），；＝＋', '(),;=+',
)

# Oracle → MySQL date format mapping
_DATE_FORMAT_MAP = {
    'YYYY-MM-DD': '%Y-%m-%d',
    'YYYYMMDD': '%Y%m%d',
    'YYYY': '%Y',
    'MM': '%m',
    'DD': '%d',
    'HH24:MI:SS': '%H:%i:%s',
    'YYYY-MM-DD HH24:MI:SS': '%Y-%m-%d %H:%i:%s',
}


def clean_sql_text(text: str) -> str:
    """Clean Excel text: fullwidth→halfwidth, fix keyword-identifier collisions."""
    if not text:
        return text
    # Fullwidth → halfwidth
    text = text.translate(_FULLWIDTH_MAP)
    # Fix keyword stuck to identifier: GUAR_CONTRACT_IDAND → GUAR_CONTRACT_ID AND
    text = re.sub(
        r'([A-Z0-9_])(AND|OR|ON|LEFT|RIGHT|INNER|JOIN|WHERE)\b',
        r'\1 \2', text, flags=re.IGNORECASE
    )
    # Fix T3.=DATE_ID → T3.DATE_ID
    text = re.sub(r'(\w)\.=(\w)', r'\1.\2', text)
    return text


def _convert_date_format(oracle_fmt: str) -> str:
    """Convert Oracle date format string to MySQL format string."""
    fmt = oracle_fmt
    # Try exact match first
    if fmt in _DATE_FORMAT_MAP:
        return _DATE_FORMAT_MAP[fmt]
    # Replace longest patterns first
    for oracle, mysql in sorted(_DATE_FORMAT_MAP.items(),
                                key=lambda x: -len(x[0])):
        fmt = fmt.replace(oracle, mysql)
    return fmt


def convert_oracle_syntax(text: str) -> str:
    """Convert Oracle SQL syntax to MySQL."""
    if not text:
        return text

    # NVL(a, b) → IFNULL(a, b)
    text = re.sub(r'\bNVL\s*\(', 'IFNULL(', text, flags=re.IGNORECASE)

    # TO_DATE('...', 'fmt') → STR_TO_DATE('...', mysql_fmt)
    def _to_date_repl(m):
        val = m.group(1)
        fmt = m.group(2)
        mysql_fmt = _convert_date_format(fmt)
        return f"STR_TO_DATE('{val}', '{mysql_fmt}')"
    text = re.sub(
        r"\bTO_DATE\s*\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)",
        _to_date_repl, text, flags=re.IGNORECASE,
    )

    # TO_CHAR(field, 'fmt') → DATE_FORMAT(field, mysql_fmt)
    def _to_char_repl(m):
        field = m.group(1).strip()
        fmt = m.group(2)
        mysql_fmt = _convert_date_format(fmt)
        return f"DATE_FORMAT({field}, '{mysql_fmt}')"
    text = re.sub(
        r"\bTO_CHAR\s*\(\s*([^,]+?)\s*,\s*'([^']+)'\s*\)",
        _to_char_repl, text, flags=re.IGNORECASE,
    )

    # DECODE(x, a, b, c, d, ..., [else]) → CASE WHEN x=a THEN b ...
    text = _convert_decode(text)

    # a || b → CONCAT(a, b)
    text = _convert_concat_operator(text)

    # SYSDATE → NOW()
    text = re.sub(r'\bSYSDATE\b', 'NOW()', text, flags=re.IGNORECASE)
    text = re.sub(r'\bSYSTIMESTAMP\b', 'NOW()', text, flags=re.IGNORECASE)

    # WM_CONCAT → GROUP_CONCAT
    text = re.sub(r'\bWM_CONCAT\s*\(', 'GROUP_CONCAT(', text,
                  flags=re.IGNORECASE)

    return text


def _convert_decode(text: str) -> str:
    """Convert DECODE(x, a, b, c, d, [else]) → CASE WHEN x=a THEN b ..."""
    pattern = re.compile(r'\bDECODE\s*\(', re.IGNORECASE)
    result = []
    pos = 0
    for m in pattern.finditer(text):
        result.append(text[pos:m.start()])
        # Find matching closing paren
        start = m.end()
        depth = 1
        i = start
        in_str = False
        while i < len(text) and depth > 0:
            ch = text[i]
            if ch == "'" and not in_str:
                in_str = True
            elif ch == "'" and in_str:
                in_str = False
            elif ch == '(' and not in_str:
                depth += 1
            elif ch == ')' and not in_str:
                depth -= 1
            i += 1
        if depth != 0:
            result.append(text[m.start():i])
            pos = i
            continue
        args_text = text[start:i - 1]
        args = _split_decode_args(args_text)
        if len(args) < 3:
            result.append(text[m.start():i])
            pos = i
            continue
        expr = args[0].strip()
        parts = ['CASE']
        j = 1
        while j + 1 < len(args):
            parts.append(f" WHEN {expr} = {args[j].strip()} THEN {args[j+1].strip()}")
            j += 2
        if j < len(args):
            parts.append(f" ELSE {args[j].strip()}")
        parts.append(' END')
        result.append(''.join(parts))
        pos = i
    result.append(text[pos:])
    return ''.join(result)


def _split_decode_args(text: str) -> list[str]:
    """Split DECODE arguments by top-level commas."""
    args = []
    depth = 0
    in_str = False
    last = 0
    for i, ch in enumerate(text):
        if ch == "'" and not in_str:
            in_str = True
        elif ch == "'" and in_str:
            in_str = False
        elif ch == '(' and not in_str:
            depth += 1
        elif ch == ')' and not in_str:
            depth -= 1
        elif ch == ',' and depth == 0 and not in_str:
            args.append(text[last:i])
            last = i + 1
    args.append(text[last:])
    return args


def _convert_concat_operator(text: str) -> str:
    """Convert Oracle || concatenation to CONCAT()."""
    if '||' not in text:
        return text
    # Split by || at top level (not inside strings or parens)
    parts = []
    current = []
    i = 0
    depth = 0
    in_str = False
    while i < len(text):
        ch = text[i]
        if ch == "'" and not in_str:
            in_str = True
            current.append(ch)
        elif ch == "'" and in_str:
            in_str = False
            current.append(ch)
        elif ch == '(' and not in_str:
            depth += 1
            current.append(ch)
        elif ch == ')' and not in_str:
            depth -= 1
            current.append(ch)
        elif text[i:i+2] == '||' and not in_str and depth == 0:
            parts.append(''.join(current).strip())
            current = []
            i += 2
            continue
        else:
            current.append(ch)
        i += 1
    parts.append(''.join(current).strip())
    if len(parts) <= 1:
        return text
    return 'CONCAT(' + ', '.join(parts) + ')'


def replace_aliases(text: str, alias_map: dict) -> str:
    """Replace table aliases in SQL text.
    E.g. A.FIELD → T1.FIELD, From C Where → From T2 Where"""
    if not text or not alias_map:
        return text
    for old, new in alias_map.items():
        # Replace A.FIELD → T1.FIELD
        text = re.sub(
            rf'\b{re.escape(old)}\.',
            f'{new}.', text
        )
        # Replace standalone alias (e.g. From C Where → From T2 Where)
        text = re.sub(
            rf'\b{re.escape(old)}\b(?!\.)',
            new, text
        )
    return text


def extract_alias(alias_raw: str, default_idx: int) -> str:
    """Extract SQL alias from Excel alias field.
    E.g. '主表 A' → 'A', 'T3 法定代表人' → 'T3', '主表A' → 'A'"""
    if not alias_raw:
        return ''
    parts = alias_raw.split()
    # Look for T\d+ pattern (space-separated or trailing)
    for p in parts:
        if re.match(r'^T\d+$', p, re.IGNORECASE):
            return p
    # Search in entire string (e.g. "关联表T3")
    m = re.search(r'(T\d+)', alias_raw, re.IGNORECASE)
    if m:
        return m.group(1)
    # Single letter alias (A, B, etc.)
    for p in parts:
        if re.match(r'^[A-Z]$', p):
            return p
    # Trailing single letter (e.g. "主表A", "关联表B")
    m = re.search(r'([A-Z])$', alias_raw)
    if m:
        return m.group(1)
    # Fallback: first token if it's an English identifier
    if parts and re.match(r'^[a-zA-Z_]\w*$', parts[0]):
        return parts[0]
    return ''


def qualify_columns(expr: str, alias: str) -> str:
    """Add table alias prefix to unqualified column names in SQL expressions.
    E.g. SPEC_ACCT_TYPE_CODE='103' → T1.SPEC_ACCT_TYPE_CODE='103'"""
    _KEYWORDS = {
        'AND', 'OR', 'NOT', 'IN', 'IS', 'NULL',
        'LIKE', 'BETWEEN', 'CASE', 'WHEN', 'THEN',
        'ELSE', 'END', 'IF', 'TRUE', 'FALSE',
        'DATE_FORMAT', 'COALESCE', 'IFNULL', 'NVL',
        'CONCAT', 'SUBSTR', 'TRIM', 'UPPER', 'LOWER',
        'SUM', 'MAX', 'MIN', 'COUNT', 'AVG',
        'GROUP_CONCAT', 'NOW', 'STR_TO_DATE',
    }

    def replacer(m):
        col = m.group(0)
        if col.upper() in _KEYWORDS:
            return col
        return f'{alias}.{col}'

    return re.sub(
        r'(?<![.\w])([A-Z_][A-Z0-9_]{2,})(?=\s*[=<>!])',
        lambda m: replacer(m), expr
    )
