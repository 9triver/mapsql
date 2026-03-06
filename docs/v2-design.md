# MapSQL v2 重构设计

## 一、v1 的问题

### 1.1 架构问题

`generate_sql.py` 是 1460 行的单体文件，4 个 class 耦合在一起：

```
ExcelParser (450行)    → 解析 Excel、段拆分、别名替换、Oracle 语法转换
CaseDictExtractor (120行) → 从手写 SQL 提取 CASE 字典
SQLGenerator (730行)   → 生成 SQL，包含 30+ 个方法
main (60行)
```

问题：
- **ExcelParser 承担太多**：既做 Excel 读取，又做文本清洗、别名替换、Oracle 语法转换
- **SQLGenerator 是上帝类**：字段映射 → SELECT 表达式的转换逻辑全在一个类里，30+ 个方法互相调用
- **规则硬编码**：Y/N→0/1 检测、日期格式转换、函数表达式识别等规则散布在代码中
- **只支持 MySQL 输出**：Oracle 现场需要不同的语法和框架
- **CaseDictExtractor 和 compare_sql.py 有重复的 SELECT 解析逻辑**

### 1.2 功能缺失

- 不支持 Oracle 输出语法
- 不支持报送平台管理字段（Oracle 现场的 +5 列）
- 字典 CASE 跨现场合并时 Oracle 语法未转换
- Web UI 不支持对照测试
- 没有单元测试

### 1.3 已验证的成果（v2 必须保留）

- Excel 解析规则经过三个现场 138 个 sheet 验证，是正确的
- 28 条检查规则是实战积累
- CASE 字典提取思路有效（211 个模式）
- 语义对照测试框架有价值

## 二、v2 架构设计

### 2.1 模块拆分

```
mapsql/
    __init__.py
    models.py          # 数据结构 (SourceTable, FieldMapping, etc.)
    excel_parser.py    # Excel 读取，只负责把 Excel 转为 models
    text_cleaner.py    # 文本清洗：全角→半角、Oracle→MySQL 语法转换
    field_resolver.py  # 字段映射 → SQL 表达式 的转换逻辑
    sql_writer.py      # SQL 拼装：存储过程框架、INSERT/SELECT/FROM/WHERE
    case_dict.py       # CASE 字典提取与查找
    compare.py         # 语义对照测试
    config.py          # 可配置项：平台类型、schema前缀、V_DATE表达式等
    cli.py             # 命令行入口
app.py                 # Web UI (Flask)
```

### 2.2 核心设计原则

**a. 解析与生成分离**
- `excel_parser.py` 只负责把 Excel → `SheetMapping` 数据结构
- `field_resolver.py` 只负责把 `FieldMapping` → SQL 表达式字符串
- `sql_writer.py` 只负责把表达式组装成完整 SQL

**b. 规则可配置**
```python
@dataclass
class GeneratorConfig:
    dialect: str = 'mysql'        # 'mysql' | 'oracle'
    schema_prefix: str = ''       # 如 'dwdevdb_model.'
    vdate_expr: str = 'I_DATE'    # V_DATE 初始化表达式
    platform_fields: list = None  # Oracle 报送平台额外字段
    proc_template: str = 'mysql'  # 存储过程框架模板
```

**c. 转换规则管道化**

`field_resolver.py` 的核心是一个规则管道，按优先级依次尝试：

```python
RULES = [
    MatchCol10CaseWhen,      # Col10 已有 CASE WHEN → 直接使用
    MatchCol7FunctionExpr,   # Col7 含函数 → NVL/SUM/MAX/GROUP_CONCAT
    MatchCol10MappingRule,   # Col10 有映射规则 → IF/CASE/转换
    MatchCol12Conditional,   # Col12 填报说明有条件 → CASE WHEN
    MatchCaseDictLookup,     # 字典查找 → 历史 CASE 映射
    MatchYNFlag,             # _FLAG Y/N→0/1
    MatchDateConversion,     # 日期格式转换
    MatchDictMismatch,       # 字典不匹配警告
    MatchDirectMapping,      # 直取字段
    MatchEmptyField,         # 空值/常量
]
```

每个 Rule 是一个独立类，有 `match()` 和 `resolve()` 方法。新增规则只需添加一个类。

**d. 文本清洗集中管理**

```python
class TextCleaner:
    """所有文本清洗规则集中在这里"""
    @staticmethod
    def fullwidth_to_halfwidth(text): ...
    @staticmethod
    def oracle_to_mysql(text): ...
    @staticmethod
    def fix_on_condition_spacing(text): ...
    @staticmethod
    def clean_field_name(text): ...
```

### 2.3 输出模板化

存储过程框架用 Jinja2 模板或字符串模板：

```
templates/
    mysql_procedure.sql.j2
    oracle_procedure.sql.j2
```

不同现场的框架差异（管理字段、日志调用、异常处理）通过模板参数化，不再硬编码。

### 2.4 CASE 字典增强

- 提取时记录来源语法类型（MySQL/Oracle）
- Oracle CASE 自动转换为 MySQL 语法后再入字典
- 支持手动维护的字典文件（YAML/JSON），不仅靠从 SQL 提取

## 三、实施计划

### Phase 1: 拆分模块（不改功能）
1. 创建 `mapsql/` 包
2. 把 `generate_sql.py` 拆分到各模块
3. 确保 `compare_sql.py` 对照结果不变（回归测试）

### Phase 2: 规则管道化
1. 实现 Rule 基类和管道
2. 把 `_gen_select_expr` 中的 if/elif 链重构为 Rule 类
3. 每个 Rule 加单元测试

### Phase 3: 多方言支持
1. 实现 `GeneratorConfig`
2. 添加 Oracle 输出模板
3. CASE 字典 Oracle→MySQL 自动转换

### Phase 4: Web UI 增强
1. 对照测试页面
2. 字典管理页面
3. 配置管理（schema、V_DATE、平台类型）
