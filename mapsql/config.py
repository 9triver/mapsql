"""Generator configuration for different deployment sites."""

from dataclasses import dataclass


@dataclass
class GeneratorConfig:
    dialect: str = 'mysql'              # 'mysql' | 'oracle'
    source_schema: str = ''             # 源表 schema 前缀 (如 'dwdevdb_model')
    target_schema: str = ''             # 目标表 schema 前缀
    proc_schema: str = ''               # 存储过程 schema
    vdate_expr: str = 'I_DATE'          # V_DATE 初始化表达式
    date_field: str = 'DATE_ID'         # 日期过滤字段名 (DATE_ID / RECORD_DATE)
    collect_date_field: str = ''        # 采集日期字段 (自动检测)
    platform_fields: bool = False       # Oracle 报送平台额外字段 (+5列)
    proc_name_pattern: str = 'Pids_t_{major}_{minor}_inner'
    table_name_pattern: str = 'ids_t_{major}_{minor}_inner'
    log_call: str = 'dwdevdb_model.PMODEL_JOB_LOG'  # 日志调用
    definer: str = ''                   # MySQL DEFINER
