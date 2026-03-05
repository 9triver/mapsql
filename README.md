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
python generate_sql.py mapping.xlsx "表8.1贷款借据"

# 生成 SQL 并写入文件，显示详细信息
python generate_sql.py mapping.xlsx "表8.1贷款借据" -o output.sql -v
```

## 设计概览

### 处理流程

```
Excel Sheet → ExcelParser → SheetMapping → SQLGenerator → SQL 存储过程
```

**ExcelParser** 解析 Excel 的四个区域：

| 区域 | 标识 | 内容 |
|------|------|------|
| 表头区 | `目标表：` | 目标表英文名、中文名 |
| 数据源表区 | `数据源表：` | 主表/关联表、别名、JOIN 类型和条件 |
| 数据范围条件区 | `数据范围条件：` | WHERE/AND 条件 |
| 字段映射区 | `字段映射` → `字段中文名` | 逐字段的源→目标映射规则 |

支持多段映射（如 A段/B段），每段独立的源表、条件和字段定义会生成独立的 INSERT...SELECT 语句。

**SQLGenerator** 根据解析结果生成完整的 MySQL 存储过程，包含：
- 变量声明和异常处理模板
- DELETE 当期数据
- 每段一个 INSERT INTO ... SELECT ... FROM ... WHERE
- 行数累计和日志记录

### 数据结构

```
SheetMapping
├── target_table        # 目标表名
├── target_cn_name      # 目标表中文名
└── segments[]          # 映射段列表
    └── MappingSegment
        ├── source_tables[]      # SourceTable: 表名、别名、JOIN 类型/条件
        ├── where_conditions[]   # WhereCondition: 操作符、条件表达式
        └── field_mappings[]     # FieldMapping: 源/目标字段、类型、映射规则
```

### 字段映射转换逻辑

按优先级从高到低处理每个字段：

| 优先级 | 条件 | 生成逻辑 |
|--------|------|----------|
| 1 | 无源表/源字段，含"采集日期" | `V_DATE` |
| 2 | 源字段 (Col7) 含函数表达式 | `NVL(...)` → `IFNULL(...)` |
| 3 | 映射规则 (Col10) 非空 | IF→CASE WHEN, TO_CHAR→DATE_FORMAT, NVL→IFNULL |
| 4 | 多源字段标记 (`__MULTI_SRC__`) | `CASE WHEN field1 > 0 OR field2 > 0 ...` |
| 5 | 填报说明 (Col12) 含"当...时" | 条件 CASE WHEN（如渠道类型判断） |
| 6 | 源为 DATE，目标为 VARCHAR | `DATE_FORMAT(..., '%Y-%m-%d')` |
| 7 | 以上均不满足 | `别名.源字段` 直取 |

### 自动检测与警告

工具会在 stderr 输出 `[注意]` 提示以下需人工关注的情况：

- **"需转换"标记**：映射规则或填报说明标注了"需转换"，但源系统码值未知
- **类型宽度不匹配**：如 VARCHAR2(6)→VARCHAR2(2) 且业务口径含码值列表，可能需要 CASE 转换
- **INTEGER→VARCHAR**：数值到字符串的隐式转换
- **多源表引用**：源表名含逗号，需人工补充取值逻辑
- **无法识别的映射规则**：在 SQL 中标记 `/* TODO: ... */`

## 工具的局限

以下场景需要人工补充 CASE 转换逻辑（因为 Excel 中缺少源系统码值信息）：

- 源/目标码值顺序不同（如贷款状态：源 04=核销 → 目标 02=核销）
- 源码值为复合编码需映射为简码（如机构类型 A030104 → 07）
- 取值依赖多表条件判断（如客户类型需区分个人/对公）

Excel 中的笔误（字段名拼写、JOIN 条件别名错误）会被原样传递到生成的 SQL 中。

## 项目结构

```
├── generate_sql.py      # 核心：Excel 解析 + SQL 生成
├── app.py               # Web UI 服务端 (Flask)
├── templates/
│   └── index.html       # Web UI 前端页面
└── docs/
    ├── mapping-rules.md          # Excel 映射解析与 SQL 生成规则（16 条检查项）
    └── existing-sql-patterns.md  # 已有手写 SQL 的 Bug 模式汇总
```
