# Excel 映射解析与 SQL 生成规则

基于表1.1、表2.1、表6.1、表7.1、表8.1 五轮实践 + generate_sql.py 工具化总结 + 两个现场共 135 个 Sheet 全量对照验证。

---

## 一、Excel Sheet 结构解析

每个 Sheet 的固定布局（从上到下）：

### 1. 表头区（前2行）

- 第1行：说明文字
- 第2行：`目标表：[目标表英文名] [目标表中文名]`

### 2. 数据源表区

- 标题行：`数据源表：表名 | 表中文名 | 表别名 | 关联类型 | 关联关系 | 备注说明`
- **第一行为主表**（无关联类型），后续行为关联表
- 关联类型：`left join` / `inner join` / `无`
- 关联关系：写的是 ON 条件（如 `T1.CUST_ID = T2.CUST_ID AND T1.DATE_ID=T2.DATE_ID`）
- **关联类型为"无"**：表示该表不参与 JOIN，仅在 WHERE 子查询中引用（如 `Not Exists(Select 1 From 表名 别名 Where ...)`）。不生成 JOIN 语句，但子查询中需用完整表名+别名
- **关键规则：所有定义的表都必须出现在生成的 SQL 中**，即使没有字段直接映射自该表
  - INNER JOIN 起数据过滤作用
  - LEFT JOIN 可能被字段引用或预留
  - 关联类型"无"在 WHERE 子查询中引用

### 3. 数据范围条件区

- 标题行：`数据范围条件：操作符 | 条件逻辑 | 逻辑说明`
- 每行一个 WHERE/AND 条件
- **所有条件都必须转为 WHERE 子句**

### 4. 字段映射区

- 标题行（Col1~Col14）：`字段中文名 | 字段英文名 | 字段类型 | 字典枚举 | 源系统 | 源表名 | 源字段英文名 | 源字段中文名 | 源字段类型 | 映射规则(直取不填) | 源系统-字典/枚举 | 填报说明 | 业务口径 | 业务范围`
- **解析优先级**：
  1. `源字段英文名`(Col7) 含函数 → 如 `NVL(T1.X,T2.X)`，需先识别并转换
  2. `映射规则`(Col10) 非空 → 有转换逻辑（IF/CASE/TO_CHAR/NVL等）
  3. `填报说明`(Col12) 含条件 → 如"当渠道类型为ATM时填交易渠道号" → CASE WHEN
  4. `填报说明`(Col12) 标注"需转换" → 等同于映射规则列标注"需转换"
  5. `字典枚举`(Col4) 非空 → 目标端有码值约束，可能需要转换
  6. `源表名`(Col6) 含逗号 → 多源表，需人工补充取值逻辑
  7. 源/目标类型宽度不匹配 → VARCHAR2(6)→VARCHAR2(2)，强信号需 CASE 转换
  8. 都为空/直取 → 简单字段映射

#### Col7 源字段的特殊格式

Col7 通常是简单字段名（如 `CUST_ID`），但也可能是：

- **NVL/IFNULL 表达式**：`NVL(T1.CUST_NAME,T2.CUST_NAME)` → 转为 `IFNULL(...)`
- **聚合函数**：`SUM(T1.AMT)`, `MAX(T1.DATE_FIELD)` → 保留函数，注意别名应在括号内（`SUM(T1.X)` 而非 `T1.SUM(X)`），且需生成 GROUP BY
- **WM_CONCAT**：`WM_CONCAT(T1.CODE)` → Oracle 函数，转为 MySQL 的 `GROUP_CONCAT(T1.CODE)`
- 需先检测是否为函数表达式，再决定处理路径

**聚合函数规则**：当 SELECT 中出现 `SUM`/`MAX`/`MIN`/`COUNT`/`GROUP_CONCAT` 等聚合函数时，必须为非聚合字段生成 `GROUP BY` 子句。

#### Col6 源表名的特殊格式

Col6 通常是单个表名（如 `DS_SAVE_ACCT`），但也可能是：

- 逗号分隔多表：`DS_ORG_CUST,DS_INDIV_CUST` → 表示取值逻辑涉及多表判断，无法自动生成

#### Col12 填报说明的隐藏逻辑

Col12 除了业务描述外，还可能包含：

- 条件映射指令：`当渠道类型为ATM机\自助终端\POS\VTM时，填交易渠道号`
- 需转换标记：`需转换`（与 Col10 等效）
- 码值说明：`103 专项存款-社保基金存款`

---

## 二、SQL 生成规则

### 1. 存储过程框架

```sql
CREATE PROCEDURE Pids_t_X_X(
    IN I_DATE VARCHAR(8),
    OUT O_RLT VARCHAR(10)
)
BEGIN
    -- 变量声明（固定模板）
    -- 异常处理（固定模板）
    -- 初始化：V_DATE = I_DATE
    -- DELETE 当期数据（按采集日期字段）
    -- INSERT INTO ... SELECT ... FROM ... WHERE
    -- 日志记录（固定模板）
END;
```

### 2. 字段映射转 SELECT 子句

#### 直取字段

源字段类型与目标字段类型一致，映射规则列为空：

```sql
T1.FIELD_NAME,
```

#### 日期格式转换

源为 DATE 类型，目标为 VARCHAR(10)：

```sql
DATE_FORMAT(T1.DATE_FIELD, '%Y-%m-%d'),
```

如果 Excel 写了"默认值9999-12-31"：

```sql
COALESCE(DATE_FORMAT(T1.DATE_FIELD, '%Y-%m-%d'), '9999-12-31'),
```

#### 字典码值转换

映射规则标注"需转换"或"转换"，且目标字典与源字典不同：

```sql
CASE
    WHEN T1.SRC_CODE = 'xxx' THEN '01'
    WHEN T1.SRC_CODE = 'yyy' THEN '02'
    ELSE NULL
END,
```

- 多个源码对应同一目标码时用 `IN`：`WHEN T1.CODE IN ('A', 'B') THEN '01'`
- **绝对不要用 AND 连接同一字段的多个等值判断**（这是已有 SQL 的常见 Bug）

#### Y/N 标志位转换

源字段为 Y/N（或 VARCHAR(1)），目标字典为 0/1。此模式极其普遍（~60% 的表中出现），
Excel 中通常通过 Col4 字典枚举 `0-否/1-是` 暗示，或 Col10/Col12 标注"需转换"：

```sql
CASE WHEN T1.FLAG_FIELD = 'Y' THEN '1' ELSE '0' END,
```

常见字段：`CONTROL_FLAG`, `ACCR_FLAG`, `CLOSE_FLAG`, `DEPOSIT_FLAG`, `RESIDENT_FLAG`,
`LISTING_FLAG`, `PEAS_HHOLD_FLAG`, `FIRST_LOAN_CUST_FLAG`, `CONT_LIAB_FLAG` 等。

**识别信号**：源字段名含 `_FLAG`，且目标字典为 `0/1` 或 `是/否`。

#### 映射规则列已有 CASE WHEN 表达式

Col10 中可能包含完整的 CASE WHEN ... END 表达式（源到目标的码值映射），此时直接使用即可，无需额外转换。需注意：

- Oracle `IS 'val'` 语法需修正为 `= 'val'`
- 裸列名需补表别名前缀
- 旧别名需替换为统一的 Tn 格式

```sql
-- Col10 原文直接输出
CASE
    WHEN T1.TYPE_CODE like '01%' THEN '03'
    WHEN T1.TYPE_CODE like '02%' THEN '01'
    ELSE '11'
END,
```

#### 条件取值

映射规则写了 IF/CASE 逻辑：

```sql
CASE WHEN T1.CONDITION = 'xxx' THEN T1.VALUE ELSE '' END,
```

#### Oracle → MySQL 语法转换

Excel 中的映射规则和条件可能使用 Oracle 语法，需统一转为 MySQL：

| Oracle 语法 | MySQL 语法 | 说明 |
|-------------|------------|------|
| `NVL(a, b)` | `IFNULL(a, b)` | 空值替换 |
| `TO_CHAR(date, 'YYYY-MM-DD')` | `DATE_FORMAT(date, '%Y-%m-%d')` | 日期格式化 |
| `TO_DATE('9999-12-31', 'YYYY-MM-DD')` | `STR_TO_DATE('9999-12-31', '%Y-%m-%d')` | 字符串转日期 |
| `WM_CONCAT(field)` | `GROUP_CONCAT(field)` | 字符串聚合 |
| `DECODE(x, a, b, c, d, e)` | `CASE WHEN x=a THEN b WHEN x=c THEN d ELSE e END` | 条件映射 |
| `IF cond THEN val1 ELSE val2` | `CASE WHEN cond THEN val1 ELSE val2 END` | PL/SQL IF → CASE |

#### COALESCE / IFNULL 取值

映射规则写了 NVL 或列出多个备选源字段：

```sql
COALESCE(T1.FIELD_A, T1.FIELD_B),
```

#### 跨表取值

源表名列不是主表：

```sql
T3.RELATED_NAME,  -- 来自关联表 T3
```

#### 常量/参数字段

无源表、源字段，如"采集日期"：

```sql
V_DATE,
```

#### 空值占位

字段暂不填报或无数据源：

```sql
'',
```

**注意**：无源字段时必须输出 `''`（空字符串）或 `NULL`，绝不能回退为 `V_DATE`。
`V_DATE` 仅用于"采集日期"等明确标注取日期参数的字段。

### 3. Excel 文本清洗

Excel 中的文本可能包含非标准字符，生成 SQL 前需清洗：

- **全角字符转半角**：`（` → `(`、`）` → `)`、`，` → `,`、`；` → `;`
- **中文伪函数转 SQL**：如条件区写 `月初(V_DATE)` → `DATE_FORMAT(V_DATE, '%Y-%m-01')`
- **特殊字符清理**：如 `DATE+ID` → `DATE_ID`（Excel 中的 `+` 号干扰）

### 4. FROM / JOIN 子句

- 按 Excel 数据源表区的定义顺序生成
- 主表放 FROM，其余按定义的关联类型生成 JOIN
- ON 条件直接使用 Excel 中的关联关系
- **不要遗漏任何一个定义的表**

### 5. WHERE 子句

- 按 Excel 数据范围条件区逐行生成
- 第一个条件用 WHERE，后续用 AND
- **别名替换**：WHERE 条件中的旧别名（A/B/C）必须同步替换为 Tn 格式
- **关联类型"无"的表在子查询中展开**：`From C Where` → `From DS_TABLE_NAME T2 Where`（因为该表不在 FROM 子句中，需用完整表名）

### 6. GROUP BY 子句

当 SELECT 中出现聚合函数（SUM/MAX/MIN/COUNT/GROUP_CONCAT）时，必须为非聚合字段生成 GROUP BY 子句。

- **排除常量表达式**：`''`（空字符串）、`V_DATE`、纯数字等不应出现在 GROUP BY 中
- 仅包含实际引用了表字段的表达式

---

## 三、常见陷阱与检查清单

### 生成时必须检查

| #  | 检查项 | 说明 |
|----|--------|------|
| 1  | **所有源表都已 JOIN** | Excel 定义了 8 个表，SQL 就必须有 8 个表 |
| 2  | **INSERT 字段数 = SELECT 字段数** | 逐一对应，注意 Excel 中字段顺序可能不连续（如跳过编号） |
| 3  | **日期字段加了 DATE_FORMAT** | 源为 DATE → 目标为 VARCHAR(10) 必须转换 |
| 4  | **映射规则列非空的字段必须有转换逻辑** | 不能直取 |
| 5  | **字典枚举列有值时检查源目标是否一致** | 不一致则需要 CASE 转换 |
| 6  | **同一源字段映射到不同目标字段** | 如 ACCT_ID 同时映射到协议ID和分户账号——合理，不是 Bug |
| 7  | **一个目标字段引用多个源字段** | 如存款账户类型优先取 ACCT_CLASSIFY_CODE，备选 ACCT_CATE_CODE → COALESCE |
| 8  | **WHERE 条件中的字段名拼写** | Excel 可能有笔误（如 TRANS_DAT 少了 E） |
| 9  | **JOIN 条件中的表别名** | Excel 可能写错别名（如 T2.DATE_ID 应为 T3.DATE_ID） |
| 10 | **映射规则为空但源目标码值不同** | 字典枚举列有值时，即使映射规则列为空，也要检查源系统码值顺序是否与目标一致。如表8.1贷款状态：源04=核销但目标02=核销，实际需要 CASE 转换 |
| 11 | **多段映射中不同段的同名字段取自不同表/不同源字段** | 如表8.1贷款利率：A段取 T1.LOAN_INT_RATE，B段取 T2.INT_RATE |
| 12 | **源字段(Col7)是否为函数表达式** | 如 `NVL(T1.X,T2.X)` 需转为 `IFNULL(...)`，不能当普通字段名处理 |
| 13 | **填报说明(Col12)是否含条件逻辑或"需转换"** | Col12 不只是注释，可能包含关键映射指令 |
| 14 | **源/目标类型宽度差异** | VARCHAR2(6)→VARCHAR2(2) 且业务口径含码值列表，几乎必然需要 CASE 转换 |
| 15 | **源表名(Col6)是否包含多表** | 如 `DS_ORG_CUST,DS_INDIV_CUST`，需人工补充取值逻辑 |
| 16 | **IF/THEN映射规则中的裸列名** | 如 `IF SPEC_ACCT_TYPE_CODE='103'` 需补表别名前缀 → `T1.SPEC_ACCT_TYPE_CODE` |
| 17 | **Y/N 标志位是否需要转 0/1** | 源字段名含 `_FLAG` 且目标字典为 `0/1`，需 `CASE WHEN 'Y' THEN '1' ELSE '0' END` |
| 18 | **V_DATE 不能作为非日期字段的值** | 只有"采集日期"等明确取日期参数的字段才用 V_DATE，其他无源字段应为 `''` |
| 19 | **源字段中文名不是 SQL 列名** | Col8 是中文注释（如"本期借方发生额"），不能当列名输出，应根据 Col7 或映射规则确定实际字段 |
| 20 | **聚合函数语法和 GROUP BY** | `SUM(T1.X)` 不能写成 `T1.SUM(X)`；有聚合函数时必须生成 GROUP BY |
| 21 | **WM_CONCAT 需转为 GROUP_CONCAT** | Oracle 聚合函数，MySQL 中不存在 |
| 22 | **全角字符需转半角** | Excel 中 `（），；` 等全角字符不是合法 SQL，需转为半角 |
| 23 | **Oracle 残留语法需全部转换** | NVL→IFNULL、TO_DATE→STR_TO_DATE、IF...THEN→CASE WHEN、DECODE→CASE |
| 24 | **别名必须统一为 Tn 格式** | Excel 中 A/B/C 别名需替换为 T1/T2/T3，否则 SQL 中出现未定义别名 |
| 25 | **Col10 已有 CASE WHEN 表达式需直接使用** | 映射规则列已写好完整 CASE WHEN ... END，直接输出，不要包装为 TODO 注释 |
| 26 | **关联类型"无"不生成 JOIN** | 该表仅在 WHERE 子查询中引用（如 Not Exists），不输出 JOIN 语句 |
| 27 | **WHERE 条件中的别名也需替换** | A/B/C 别名不仅在 JOIN 和 SELECT 中替换，WHERE 条件中同样需要 |
| 28 | **GROUP BY 排除常量** | `''`、`V_DATE`、数字等常量不应出现在 GROUP BY 中 |

### 别名提取与统一规则

Excel 别名列格式多样，提取优先级：

1. 匹配 `T\d+` 模式（如 "T3 法定代表人" → `T3`）
2. 匹配单大写字母（如 "主表 A" → `A`）
3. 第一个 token 为纯英文标识符时取该 token
4. 纯中文（如 "主表"）→ 返回空，由程序自动分配 `T{n}`

**别名统一**：生成 SQL 时，所有表别名应统一为 `T1`, `T2`, ... 格式。如果 Excel 使用了 A/B/C 等单字母别名，需在字段映射、JOIN 条件和 WHERE 条件中同步替换为对应的 `Tn` 别名，否则会产生未定义别名错误。别名替换需匹配两种模式：`A.FIELD`（别名.字段）和独立的 `A`（如子查询中的 `From A Where`）。

### Excel 常见笔误模式（需人工判断修正）

- 关联条件中表别名写错（复制粘贴问题，如 T2.DATE_ID 应为 T3.DATE_ID）
- WHERE 字段名少字母（如 TRANS_DAT 应为 TRANS_DATE）
- "映射规则"列写了 Oracle 语法（TO_CHAR），需转为 MySQL（DATE_FORMAT）
- 源字段与目标字段语义不匹配（如 B010047 环境风险分类映射到了统一社会信用代码）
- **"需转换"标记位置不固定**：可能在 Col10（映射规则）也可能在 Col12（填报说明）
- ON 条件中特殊字符干扰（如 `DATE+ID` 应为 `DATE_ID`）
- ON 条件缺少空格导致关键字拼接（如 `GUAR_CONTRACT_IDAND` 应为 `GUAR_CONTRACT_ID AND`）
- 全角括号混入 SQL 表达式（如 `IN（'01','02'）` 应为 `IN ('01','02')`）

---

## 四、自动生成 SQL 与实际存储过程的已知差异

基于三个现场的全量语义对照（`compare_sql.py`）：

| 现场 | 类型 | Sheet 数 | 通过 | 有差异 |
|------|------|---------|------|--------|
| 一表通-20260304 | MySQL | 5 | 2 (40%) | 3 |
| 全量映射-20251119 | MySQL | 60 | 21 (35%) | 39 |
| 另一个现场-20251011 | Oracle | 73 | 0 (0%) | 73 |

### 4.1 Excel 映射定义能覆盖的部分（自动生成质量高）

- INSERT 字段列表：与手写一致（MySQL 现场 100%，Oracle 现场差固定 5 列见 4.4）
- INSERT/SELECT 列数平衡：生成 SQL 无不平衡（手写 SQL 反而有不平衡 bug）
- 源表选取和 JOIN 结构：与 Excel 定义一致
- WHERE 条件和 RECORD_DATE 过滤：正确
- 多段映射（A段/B段）结构识别：正确
- `_FLAG` Y/N→0/1 自动转换：生成器比手写 SQL 更完整（手写经常遗漏）
- `--dict-from` CASE 字典匹配：可从手写 SQL 提取约 100 个码值映射模式

### 4.2 Excel 不定义、需现场补充的逻辑

| 定制项 | 说明 | 影响面 |
|--------|------|--------|
| **机构ID前缀** | 现场需在 BANK_ORG_ID 前拼接机构编码（如 `'B0308H23100' \|\|`）| 全部表 |
| **活跃客户过滤** | 限定客户在 12 张账户/关系表中有记录才纳入报送 | 表2-3系列全部 |
| **SYS_SOURCE 过滤** | 按数据来源系统过滤（如 '贸易融资'、'BNDH'、'REPDL'）| 表7-8系列多个 |
| **码值 CASE 映射** | Y/N→1/0、证件类型函数、客户类型/协议状态/担保类型等码值转换 | ~60% 的表 |
| **交易对手分类** | 需外部查询表（如 UPLOAD_WIND、GMP_GMPCCPTY）| 表7.5, 7.6, 7.7 |
| **担保方式聚合** | LISTAGG/GROUP_CONCAT 聚合多条担保记录 | 表6.2, 6.27 |
| **去重逻辑** | ROW_NUMBER() / SELECT DISTINCT | 表1.3, 3.1, 4.3, 6.8 |
| **额外数据段** | 部分现场有 Excel 未定义的补充数据源段 | 表4.3, 7.2, 8.7, 8.9, 8.15 |
| **V_DATE 初始化** | `I_DATE` vs `DATE_SUB(I_DATE, INTERVAL 1 DAY)`，因现场而异 | 全部表 |
| **Schema 前缀** | `dwdevdb_model.DS_xxx` vs `IDL.DS_xxx`，因现场而异 | 全部表 |

### 4.3 已有手写 SQL 中的 Bug 模式（多个现场均出现）

| 差异类型 | 原因 | 处理 |
|----------|------|------|
| AND 应为 OR/IN | 同一字段多个等值用了 AND（永远为 false） | Bug，修正为 IN |
| LIKE 缺少通配符 | 如 `LIKE '03'` 应为 `LIKE '03%'` | Bug，补上 % |
| 表别名错误 | WHERE 中用了未定义的别名 | Bug，修正别名 |
| INSERT 列重复/错位 | 复制粘贴导致 INSERT 列名重复或映射错位一行 | Bug，如表1.6、2.6、3.4 |
| 段源表复制粘贴错 | 多段映射中后续段复制第一段后忘改源表 | Bug，如表4.3 |
| WHERE 括号缺失 | OR 优先级问题导致条件逻辑错误 | Bug，如表3.2 |
| 函数名拼写错误 | `DATE_FORMATE`、`V_DATE_DATE` 等 | Bug，修正拼写 |
| JOIN 键用错 | 如 `ACCT_ID` 应为 `AGREE_ID` | Bug，如表7.2 |
| GROUP BY 缺聚合函数 | SELECT 有 GROUP BY 但非分组字段未加 MAX() | Bug，如表1.6 段2 |
| 表8.6/8.7 内容互换 | INSERT 列数对调（35 vs 25），怀疑复制粘贴搞反 | Bug |
| 注释与实际不符 | 复制模板后忘改功能描述 | 非关键 |
| 目标表名差异 | 内部表名 `ids_t_X_X_inner` vs 报送表名 `YBT2_XXX` vs Oracle `T_X_X` | 命名规范差异 |

### 4.4 Oracle 现场的特殊架构（另一个现场，73 个 sheet 全部涉及）

Oracle 现场与 MySQL 现场的核心差异不在 Excel 映射规则，而在**部署架构**：

#### 报送平台管理字段（+5 列）

每个 INSERT 比 Excel 映射多 5 个平台管理字段：

```sql
INSERT INTO YBT.T_1_1 (
    ID,                -- 序列号 Seq_fitech.nextval
    REPORTID,          -- 报表ID，来自 REPORTTEMPLATE 关联
    ...,               -- Excel 定义的字段
    BUSINESSCODE,      -- 业务代码（固定值）
    INSTITUTIONCODE,   -- 机构代码，来自 INSTITUTION 关联
    TERM               -- 报送期次（如 '202510'）
)
```

对应多 2 个关联表：`REPORTTEMPLATE`（获取 REPORTID）、`INSTITUTION`（获取 INSTITUTIONCODE）。

#### 二阶段架构（中间表）

Oracle 现场采用先 ETL 到中间表、再从中间表加载的模式：
- 阶段一：源表 `IDL.DS_BANK_ORG` → 中间表 `T_1_1`（字段已按目标格式重命名）
- 阶段二：`SELECT T1.A010001 FROM T_1_1 T1` → 最终表 `YBT.T_1_1`

generate_sql.py 生成的是阶段一的逻辑（源字段→目标字段映射），而手写 SQL 是阶段二（已在中间表中用目标字段名）。

#### 交易表（7.x 系列）加载方式不同

Oracle 现场的 7.1~7.12 交易表未使用 INSERT...SELECT 结构（可能使用 MERGE INTO 或外部工具加载），导致对照测试无法匹配。

### 4.5 语义对照测试差异分类

基于 `compare_sql.py` 对三个现场的对照结果，差异来源汇总：

| 差异来源 | 一表通-20260304 | 全量映射 | 另一个现场 | 性质 |
|----------|:-:|:-:|:-:|------|
| 报送平台管理字段 | 0 | 0 | 73 | 现场架构差异 |
| 二阶段架构（中间表） | 0 | 0 | ~36 | 现场架构差异 |
| 报送平台关联表 | 0 | 0 | ~56 | 现场架构差异 |
| 交易表非 INSERT-SELECT | 0 | 0 | ~15 | 现场架构差异 |
| 多出 _FLAG CASE（生成更完整） | 3 | ~20 | ~28 | 生成器优势 |
| 缺少码值 CASE（需字典） | 1 | ~13 | ~4 | 已知局限 |
| 手写 SQL bug | 0 | ~5 | ~3 | 手写质量问题 |
