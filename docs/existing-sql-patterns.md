# 已有 SQL 的框架约定和常见 Bug 模式

## 框架约定

### 存储过程命名

- 过程名: `Pids_t_{大表号}_{小表号}_inner`
- 目标表名: `ids_t_{大表号}_{小表号}_inner`
- 所属 schema: `dwdevdb_ids`
- 源表 schema: `dwdevdb_model`

### V_DATE 处理

已有 SQL 统一使用 **前一天**：

```sql
SET V_DATE = date_format(DATE_SUB(I_DATE, INTERVAL 1 DAY), '%Y%m%d');
```

Excel 映射中无此规则，需与业务方确认。

### 标准变量声明

```sql
DECLARE V_DATE VARCHAR(8);
DECLARE V_START_DT DATETIME;
DECLARE V_PRD_NAME VARCHAR(100);
DECLARE V_TAB_NAME VARCHAR(100);
DECLARE V_STATE VARCHAR(10);
DECLARE V_TOTAL_NUM INT DEFAULT 0;
DECLARE V_TAGS VARCHAR(300);
DECLARE V_MSG TEXT;
```

### 日志记录

使用 `dwdevdb_model.PMODEL_JOB_LOG` 存储过程记录执行日志。

---

## 已发现的 Bug 模式（五轮对照汇总）

### 1. 逻辑运算符错误 — AND 应为 OR/IN

**表1.1 L89**: `FINAC_ORG_TYPE_CODE = 'A0402' AND ... = 'A040201' AND ... = 'A040202'`

- 同一字段不可能同时等于三个不同值
- 应为 `IN ('A0402', 'A040201', 'A040202')`

### 2. LIKE 缺少通配符

**表1.1 L101**: `LIKE '03'` 应为 `LIKE '03%'`

### 3. 表别名错误

**表1.1 L133**: `where A.RECORD_DATE = V_DATE` — 表别名是 T1 不是 A

### 4. 跨表引用错误

**表6.1 L105**: 其他介质启用日期用了 `T6.STRT_USING_DATE`（存折表），应为 `T7.STRT_USING_DATE`（其他介质表）

### 5. 字段语义错误

**表6.1 L114**: 特定养老储蓄存款标识(F010050) 直接取了 `SAVE_PROD_STYLE_CODE`（多值代码01~21），应为 0/1 标识

### 6. 注释复制未更新

**表6.1 L8**: 功能描述写的是"重要股东及主要关联企业表"，实际是"存款协议"

### 7. 映射规则被注释掉

**表2.1 L73-74**: B010007 的 `DATE_FORMAT(T1.REG_UPDATE_DATE,'%Y-%m-%d')` 被注释掉，直接用空字符串 `''`

### 8. 渠道码值错误

**表7.1 L88**: 交易终端ID 的渠道判断用了 `'02','03','07','09'`，按 Excel 字典应为 `'02','03','04'`（ATM/VTM/POS）

### 9. 遗漏关联表

**表6.1**: Excel 定义了 `INNER JOIN DS_PROD T8`，已有 SQL 未 JOIN（数据范围可能偏大）

### 10. WHERE 条件字段名/别名不一致

**表1.1**: Excel 用 `DATE_ID`，已有 SQL 用 `RECORD_DATE` — 需确认实际表结构
