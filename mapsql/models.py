"""Data structures for Excel mapping definitions."""

from dataclasses import dataclass, field


@dataclass
class SourceTable:
    """数据源表定义"""
    table_name: str          # 表英文名
    table_cn_name: str       # 表中文名
    alias: str               # 别名 (T1, T2, ...)
    join_type: str           # 关联类型: '' (主表) / 'LEFT JOIN' / 'INNER JOIN'
    join_condition: str      # 关联条件 (ON ...)
    remark: str = ''         # 备注


@dataclass
class WhereCondition:
    """数据范围条件"""
    operator: str            # WHERE / AND
    condition: str           # 条件表达式
    description: str = ''    # 逻辑说明


@dataclass
class FieldMapping:
    """字段映射"""
    target_cn_name: str      # 目标字段中文名
    target_en_name: str      # 目标字段英文名
    target_type: str         # 目标字段类型
    target_dict: str         # 字典枚举
    source_table: str        # 源表名
    source_field: str        # 源字段英文名
    source_cn_name: str      # 源字段中文名
    source_type: str         # 源字段类型
    mapping_rule: str        # 映射规则
    source_dict: str         # 源系统字典
    fill_instruction: str    # 填报说明（Col12）
    description: str         # 业务口径（Col13）
    biz_scope: str           # 业务范围（Col14）


@dataclass
class MappingSegment:
    """一段映射块（A段/B段等）"""
    segment_name: str                          # 段名称
    source_tables: list = field(default_factory=list)   # 数据源表列表
    where_conditions: list = field(default_factory=list) # WHERE 条件列表
    field_mappings: list = field(default_factory=list)   # 字段映射列表
    alias_map: dict = field(default_factory=dict)  # 旧别名→新别名映射


@dataclass
class SheetMapping:
    """整个 Sheet 的映射定义"""
    target_table: str        # 目标表英文名
    target_cn_name: str      # 目标表中文名
    segments: list = field(default_factory=list)  # 映射段列表
