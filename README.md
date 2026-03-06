# MapSQL — YBT 监管报表映射 SQL 生成工具

将 Excel 定义的 YBT（一表通）监管报表映射关系，自动生成 MySQL 存储过程 SQL。

## 快速开始

```bash
pip install openpyxl flask
```

### Web UI

```bash
python app.py
# 浏览器打开 http://127.0.0.1:6000
```

上传 Excel → 查看各 Sheet 内容 → 校验映射定义 → 生成 SQL → 下载。

### 命令行

```bash
# 生成 SQL 并输出到终端
python3 -m mapsql.cli mapping.xlsx "表8.1贷款借据"

# 使用手写 SQL 中的 CASE 映射字典
python3 -m mapsql.cli mapping.xlsx "表8.1贷款借据" --dict-from ./手写SQL目录

# 生成 SQL 并写入文件，显示详细信息
python3 -m mapsql.cli mapping.xlsx "表8.1贷款借据" -o output.sql -v

# 对照测试：生成 SQL vs 手写 SQL
python3 -m mapsql.compare mapping.xlsx ./手写SQL目录
```

## 架构

```
Excel Sheet → ExcelParser → SheetMapping → FieldResolver (Rule Pipeline) → SQLWriter → SQL 存储过程
                                                  ↑
                                            CaseDictExtractor (dict-from)
```

### 模块说明

| 模块 | 职责 |
|------|------|
| `mapsql/models.py` | 数据结构定义（SourceTable, FieldMapping, MappingSegment, SheetMapping） |
| `mapsql/config.py` | 现场配置（GeneratorConfig: schema, dialect, proc_name 等） |
| `mapsql/excel_parser.py` | Excel 解析：表头、数据源表、WHERE 条件、字段映射四个区域 |
| `mapsql/text_cleaner.py` | 文本清洗：全角转半角、Oracle→MySQL 语法转换、别名替换 |
| `mapsql/field_resolver.py` | 字段解析：9 条规则管道，将每个字段映射转为 SELECT 表达式 |
| `mapsql/case_dict.py` | CASE 字典：从手写 SQL 文件中提取 CASE WHEN 映射 |
| `mapsql/sql_writer.py` | SQL 组装：存储过程模板 + 各段 INSERT...SELECT + 生成摘要 |
| `mapsql/compare.py` | 对照测试：生成 SQL 与手写 SQL 的语义结构比较 |
| `mapsql/cli.py` | 命令行入口 |
| `app.py` | Web UI 服务端（Flask） |

### 数据结构

```
SheetMapping
├── target_table        # 目标表名
├── target_cn_name      # 目标表中文名
└── segments[]          # 映射段列表
    └── MappingSegment
        ├── segment_name         # 段名称（A段/B段/默认段）
        ├── source_tables[]      # SourceTable: 表名、别名、JOIN 类型/条件
        ├── where_conditions[]   # WhereCondition: 操作符、条件表达式
        ├── field_mappings[]     # FieldMapping: 源/目标字段、类型、映射规则
        └── alias_map            # 别名替换映射（A→T1, B→T2）
```

## 字段映射规则管道

每个目标字段按优先级依次匹配以下 9 条规则，**命中即停**：

| 优先级 | 规则 | 匹配条件 | 生成结果 |
|--------|------|----------|----------|
| 1 | **采集日期** | 源字段为 `V_DATE`，或目标含"采集日期" | `V_DATE` |
| 2 | **Col10 CASE** | 映射规则 (Col10) 以 `CASE` 开头 | 直接使用，修正 `IS`→`=`、补别名、Oracle→MySQL |
| 3 | **函数表达式** | 源字段 (Col7) 含 `NVL()`/`SUM()`/`MAX()` 等 | 函数转换 + 别名补全 |
| 4 | **映射规则** | Col10 非空（非 CASE） | `IF→CASE WHEN`，`TO_CHAR→DATE_FORMAT`，`DECODE→CASE`，`NVL→IFNULL`，`\|\|→CONCAT`；中文说明/子查询→TODO |
| 5 | **日期转换** | 源类型含 DATE，目标类型为 VARCHAR | `DATE_FORMAT(T1.field, '%Y-%m-%d')` |
| 6 | **FLAG 标志** | 源字段含 `_FLAG`，目标字典为 0/1 | `CASE WHEN T1.field='Y' THEN '1' ELSE '0' END` |
| 7 | **码值映射** | 源/目标类型宽度不匹配（如 VARCHAR(6)→VARCHAR(2)） | 三级策略（见下文） |
| 8 | **直取字段** | 有源字段，无上述特殊情况 | `T1.field` |
| 9 | **空值兜底** | 以上均不匹配 | `''` |

## CASE 字典三级策略

处理源/目标码值不同需要 CASE 映射的场景（规则 7），按优先级递进：

**级别 1：dict-from 历史提取**
从手写 SQL 文件提取已有 CASE 映射，按 `(目标字段, 源字段)` 匹配。命中则直接使用完整 CASE 表达式。

```bash
python3 -m mapsql.cli mapping.xlsx "表8.1贷款借据" --dict-from ./手写SQL目录
```

**级别 2：Col4/Col13 码值骨架**
从 Excel 的字典枚举（Col4）或填报说明（Col13）提取目标码值，生成 CASE 骨架，WHEN 条件留空供用户填写：

```sql
CASE T1.SOURCE_FIELD
    WHEN '' THEN '01'  -- 央行
    WHEN '' THEN '02'  -- 政策性银行
    WHEN '' THEN '03'  -- 大型商业银行
    ELSE ''
END  /* TODO: 请根据源系统字典填写 WHEN 条件值 */
```

**级别 3：直取 + TODO 警告**
无码值信息时直取源字段，附加 TODO 注释提醒人工补充。

## Oracle→MySQL 语法转换

文本清洗管道自动处理 Excel 中的 Oracle 语法：

| Oracle | MySQL |
|--------|-------|
| `NVL(a, b)` | `IFNULL(a, b)` |
| `TO_CHAR(d, 'YYYY-MM-DD')` | `DATE_FORMAT(d, '%Y-%m-%d')` |
| `TO_DATE('...', 'YYYY-MM-DD')` | `STR_TO_DATE('...', '%Y-%m-%d')` |
| `DECODE(x, a, b, c, d, e)` | `CASE WHEN x=a THEN b WHEN x=c THEN d ELSE e END` |
| `a \|\| b` | `CONCAT(a, b)` |
| `SYSDATE` / `SYSTIMESTAMP` | `NOW()` |
| `WM_CONCAT(f)` | `GROUP_CONCAT(f)` |
| `IF c THEN v1 ELSE v2 END IF` | `CASE WHEN c THEN v1 ELSE v2 END` |

全角字符（`（），；`）自动转半角，关键字粘连（`IDAND`→`ID AND`）自动修复。

## TODO/REVIEW 注释

生成的 SQL 中，不确定的部分用统一格式标注：

| 级别 | 格式 | 含义 |
|------|------|------|
| **TODO** | `/* TODO: 描述 */` | 必须人工补充，否则 SQL 不完整 |
| **REVIEW** | `/* REVIEW: 描述 */` | 建议人工确认，可能已正确 |

常见 TODO 场景：码值 CASE 骨架（WHEN 留空）、类型宽度不匹配、映射规则为文字说明、多源表取值。

## 生成摘要

每个存储过程末尾自动生成统计摘要：

```sql
/* ================================================================
   自动生成摘要
   --------------------------------------------------------------
   目标表: YBT2_JGL_JGXX (表1.1机构信息)
   段数: 1
   总字段: 23
   --------------------------------------------------------------
   自动映射: 20 个字段 (86%)
     - 直取字段: 13
     - 日期转换: 1
     - FLAG转换: 5
     - V_DATE: 1
   需人工确认: 3 个字段 (13%)
     - 码值CASE骨架(WHEN留空): 3  -> 搜索 "TODO: 请根据源系统字典"
   ================================================================ */
```

## 现场配置

通过 `GeneratorConfig` 参数化不同现场的差异：

```python
from mapsql.config import GeneratorConfig

config = GeneratorConfig(
    dialect='mysql',
    source_schema='dwdevdb_model',     # 源表 schema 前缀
    target_schema='dwdevdb_ids',       # 目标表 schema 前缀
    proc_schema='dwdevdb_ids',         # 存储过程 schema
    vdate_expr='I_DATE',               # V_DATE 初始化表达式
    log_call='dwdevdb_model.PMODEL_JOB_LOG',  # 日志调用
    definer='`dwdev`@`%`',            # MySQL DEFINER
)
```

## 项目结构

```
mapsql/
├── __init__.py          # 包入口，re-export 关键类
├── models.py            # 数据结构定义
├── config.py            # GeneratorConfig 现场配置
├── excel_parser.py      # Excel 解析
├── text_cleaner.py      # 文本清洗 + Oracle→MySQL 转换
├── field_resolver.py    # 规则管道（9 条规则）
├── case_dict.py         # CASE 映射字典提取
├── sql_writer.py        # SQL 存储过程组装
├── compare.py           # 对照测试
└── cli.py               # 命令行入口
app.py                   # Web UI (Flask)
templates/
└── index.html           # Web UI 前端
docs/
├── v2-generation-rules.md    # 生成规则详细文档
├── v2-design.md              # 模块架构设计文档
├── mapping-rules.md          # v1 映射规则参考
└── existing-sql-patterns.md  # 已有 SQL Bug 模式汇总
```
