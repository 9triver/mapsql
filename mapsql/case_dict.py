"""CASE dictionary: extract and lookup CASE WHEN mappings from handwritten SQL."""

import os
import re
from typing import Optional


class CaseDictExtractor:
    """Extract CASE WHEN mappings from handwritten SQL files.

    Dictionary key is (target_field, source_field), value is the CASE expression string.
    """

    def __init__(self):
        self.case_dict: dict[tuple[str, str], str] = {}

    @staticmethod
    def _parse_select_exprs(select_text: str) -> list[str]:
        """Split SELECT expressions by top-level commas, handling CASE/paren nesting."""
        # Strip line-end SQL comments (preserving strings)
        lines = select_text.split('\n')
        clean_lines = []
        for line in lines:
            in_str = False
            result = []
            i = 0
            while i < len(line):
                if line[i] == "'" and not in_str:
                    in_str = True
                elif line[i] == "'" and in_str:
                    in_str = False
                elif line[i:i+2] == '--' and not in_str:
                    break
                result.append(line[i])
                i += 1
            clean_lines.append(''.join(result).rstrip())
        cleaned = '\n'.join(clean_lines)

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

    def load_from_directory(self, dir_path: str) -> int:
        """Load CASE mappings from all .txt/.sql files in a directory. Returns count."""
        count = 0
        for fname in sorted(os.listdir(dir_path)):
            if not fname.endswith(('.txt', '.sql')):
                continue
            filepath = os.path.join(dir_path, fname)
            count += self._extract_from_file(filepath)
        return count

    def _extract_from_file(self, filepath: str) -> int:
        with open(filepath, encoding='utf-8') as f:
            text = f.read()

        count = 0
        for m in re.finditer(
            r'insert\s+(?:/\*.*?\*/\s*)?into\s+[\w.]+\s*\(([^)]+)\)\s*\n?\s*select\s+(.+?)\n\s*from\s',
            text, re.DOTALL | re.IGNORECASE,
        ):
            raw_cols = m.group(1)
            raw_cols = re.sub(r'--[^\n]*', '', raw_cols)
            cols = [c.strip() for c in raw_cols.split(',') if c.strip()]
            exprs = self._parse_select_exprs(m.group(2))

            for j in range(min(len(cols), len(exprs))):
                expr = exprs[j].strip()
                expr_clean = re.sub(
                    r'\s*--[^\n]*$', '', expr, flags=re.MULTILINE
                ).strip()
                if not re.match(r'CASE\b', expr_clean, re.IGNORECASE):
                    continue
                col = cols[j].strip()
                src_m = re.search(r'\bT\d+\.(\w+)', expr_clean)
                if not src_m:
                    continue
                src_field = src_m.group(1)
                key = (col, src_field)
                if key not in self.case_dict:
                    self.case_dict[key] = expr_clean
                    count += 1
        return count

    def lookup(self, target_field: str, source_field: str) -> Optional[str]:
        """Lookup a CASE mapping. Returns the CASE expression or None."""
        return self.case_dict.get((target_field, source_field))
