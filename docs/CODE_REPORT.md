# NL2SQL V0.5 代码与架构报告

> 历史架构快照。V0.9 已增加多轮会话、图表切换和实体别名 ID 绑定；当前功能与限制以根目录 README 为准。

> 编写日期：2026-09-15
> 项目仓库：<https://github.com/lekee0809/NL2SQL>
> 当前技术路线：PostgreSQL + FastAPI + 千问 QuerySpec + 后端安全 SQL 编译

## 1. 报告目的

本文档用于记录当前项目的真实实现，方便后续继续开发、排查问题、编写论文或向其他开发者说明项目。内容以现有代码为准，既描述已经完成的能力，也明确尚未完成的部分。

当前版本是一个可运行的中文智能问数原型。用户输入自然语言问题后，系统调用千问生成结构化 `QuerySpec`，再由后端根据业务字典生成参数化 PostgreSQL 查询。模型默认不直接输出 SQL、表名或列名。

## 2. 总体架构

```text
┌─────────────────────────────────────────────────────────────┐
│                         浏览器前端                           │
│  问数页面 / 候选澄清 / 表格 / 基础图表 / SQL / 数据源管理   │
└─────────────────────────────┬───────────────────────────────┘
                              │ HTTP + JSON
┌─────────────────────────────▼───────────────────────────────┐
│                      FastAPI 接口层                         │
│ /query  /query/resolve  /catalog/search  /sources/*         │
└───────────────┬─────────────────────────────┬───────────────┘
                │                             │
┌───────────────▼──────────────┐  ┌──────────▼───────────────┐
│       查询理解与生成层        │  │      元数据目录层         │
│ 本地检索 → 千问 → QuerySpec   │  │ 扫描 Schema / 生成文档    │
└───────────────┬──────────────┘  └──────────┬───────────────┘
                │                             │
┌───────────────▼─────────────────────────────▼───────────────┐
│                     后端确定性处理层                        │
│ 实体值解析 → SQL 编译 → AST 只读校验 → 参数化执行           │
└─────────────────────────────┬───────────────────────────────┘
                              │ 只读事务
┌─────────────────────────────▼───────────────────────────────┐
│                         PostgreSQL                          │
│ customers / products / orders / order_items / payments     │
└─────────────────────────────────────────────────────────────┘
```

### 2.1 默认查询链路

```text
用户问题
→ 本地 Catalog 检索指标与维度
→ 本地判断问题中可能存在的商品/客户实体
→ 构造精简 Prompt
→ 千问返回严格 JSON QuerySpec
→ 实体值解析与候选澄清
→ 后端根据业务字典编译 SQL 和参数
→ SQLGlot AST 只读校验
→ PostgreSQL 只读事务执行
→ 前端显示结果
```

### 2.2 关键设计原则

1. 模型负责理解意图，不负责决定真实表名、列名、JOIN 和 SQL 片段。
2. 后端只接受业务字典中登记过的指标和维度 ID。
3. 用户提供的过滤值使用参数传递，不直接拼进 SQL。
4. 数据库执行层始终开启只读事务。
5. 候选澄清继续查询时不再次调用模型。
6. Catalog 检索和实体候选查询均在本地完成。

## 3. 项目目录

```text
NL2SQL/
├─ app/                         后端核心代码
│  ├─ main.py                   FastAPI 入口和路由
│  ├─ config.py                 环境变量配置
│  ├─ llm.py                    千问调用与 Prompt 构造
│  ├─ query_spec.py             查询计划模型和 SQL 编译器
│  ├─ value_resolver.py         数据库字段值解析与澄清
│  ├─ database.py               PostgreSQL 访问
│  ├─ sql_guard.py              SQL AST 安全校验
│  ├─ catalog_models.py         统一元数据模型
│  ├─ catalog_builder.py        PostgreSQL 扫描和目录生成
│  ├─ catalog_retrieval.py      本地目录检索
│  └─ source_registry.py        数据源登记与扫描编排
├─ static/                      原生网页前端
│  ├─ index.html                问数主页面
│  ├─ app.js                    问数交互逻辑
│  ├─ styles.css                主页面样式
│  ├─ resolver.css              澄清候选样式
│  ├─ admin.html                数据源管理页面
│  ├─ admin.js                  数据源管理交互
│  └─ admin.css                 管理页面样式
├─ db/                          数据库 SQL
├─ catalog/                     数据源登记与统一目录
├─ scripts/                     命令行辅助程序
├─ eval/                        数据集、评测器和报告
├─ tests/                       单元测试
├─ business_dictionary.json     当前电商库业务字典
├─ requirements.txt             Python 依赖
└─ .env.example                 环境变量示例
```

## 4. 后端模块说明

### 4.1 `app/main.py`：应用入口与接口编排

该模块创建 FastAPI 应用、挂载静态文件，并将其他模块串成完整流程。

主要职责：

- 返回问数页面和数据源管理页面；
- 提供健康检查；
- 提供 Catalog 检索接口；
- 提供数据源状态、连接测试和扫描接口；
- 提供 V1、V2 查询接口；
- 捕获安全错误、澄清请求、运行错误并转换为 HTTP 状态码；
- 缓存本地 `CatalogRetriever`，重新扫描后清除缓存。

请求模型：

| 模型 | 字段 | 作用 |
|---|---|---|
| `QueryRequest` | `question` | 用户问题，长度 1～500 |
| `ResolveRequest` | `query_spec`、`filter_index`、`value` | 用户确认候选值后继续查询 |
| `CatalogSearchRequest` | `query`、`document_types`、`limit` | 本地目录检索 |
| `SourceScanRequest` | `include_value_samples`、`max_sample_values` | 控制数据库扫描 |

主要接口：

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | `/` | 返回 `static/index.html` |
| GET | `/admin` | 返回 `static/admin.html` |
| GET | `/health` | 检查 API、数据库和检索配置 |
| POST | `/catalog/search` | 搜索本地目录文档 |
| GET | `/sources` | 返回已登记数据源状态 |
| POST | `/sources/{source_id}/test` | 测试 PostgreSQL 连接 |
| POST | `/sources/{source_id}/scan` | 只读扫描数据库并重建目录 |
| POST | `/query/v1` | 旧版模型直出 SQL 基线 |
| POST | `/query`、`/query/v2` | 默认 QuerySpec 查询链路 |
| POST | `/query/resolve` | 使用用户选择的候选值继续执行 |

`execute_query_spec()` 是 V2 的后半段编排函数：

```text
QuerySpec
→ resolve_query_spec()
→ compile_query()
→ validate_readonly_sql()
→ execute_readonly()
→ JSON 响应
```

返回内容包括最终 `query_spec`、自动值修正记录、SQL、参数、查询行和行数，便于调试和评测。

### 4.2 `app/config.py`：运行配置

使用 `python-dotenv` 读取 `.env`，并通过不可变 `Settings` 数据类集中保存配置。

| 环境变量 | 作用 | 默认值 |
|---|---|---|
| `DATABASE_URL` | 默认 PostgreSQL 连接串 | 空 |
| `DASHSCOPE_API_KEY` | 阿里云百炼 API Key | 空 |
| `LLM_BASE_URL` | OpenAI 兼容接口地址 | DashScope compatible-mode |
| `LLM_MODEL` | 使用的千问模型 | `qwen-plus` |
| `LLM_TIMEOUT_SECONDS` | 模型请求超时 | 30 秒 |
| `DB_STATEMENT_TIMEOUT_MS` | SQL 执行超时 | 5000 毫秒 |
| `MAX_RESULT_ROWS` | 最大读取行数 | 200 |
| `CATALOG_RETRIEVAL_ENABLED` | 是否启用精简目录检索 | false |
| `CATALOG_RETRIEVAL_TOP_K` | 目录检索数量 | 10 |

代码兼容早期的 `OPENAI_API_KEY` 和 `OPENAI_MODEL` 名称，但实际请求地址由 `LLM_BASE_URL` 决定。本项目当前配置为阿里云百炼，不是 OpenAI 服务。

### 4.3 `app/llm.py`：Prompt 与模型调用

该模块包含两条模型链路。

#### V1：`generate_sql()`

向模型发送：

- 用户问题；
- 完整 `db/schema.sql`；
- 完整 `business_dictionary.json`。

模型直接返回 SQL。此链路仅作为对照实验保留，风险和扩展性均弱于 V2。

#### V2：`generate_query_spec()`

向模型发送：

- 当前日期基准；
- 用户问题；
- 本地检索后的精简业务目录；
- QuerySpec JSON Schema。

请求使用：

- `temperature=0`，减少随机性；
- `enable_thinking=false`，减少延迟和 token；
- `response_format=json_schema`，约束输出结构。

`build_prompt_catalog()` 根据问题在本地检索相关指标和维度，只将候选业务定义放入 Prompt。当检索不到任何指标时，会回退完整业务目录，避免完全无法查询。

本地实体提示会检查问题是否与商品或客户候选值有明显重合，并把相应维度类型加入精简目录。它不会把整批数据库候选值返回给模型。

每次首次调用 `/query` 通常产生一次模型调用；调用 `/query/resolve` 不会再次调用模型。

### 4.4 `app/query_spec.py`：查询协议和 SQL 编译器

这是当前安全架构的中心模块。

#### QuerySpec 结构

```json
{
  "metrics": ["sales_amount"],
  "dimensions": ["product"],
  "filters": [
    {
      "field": "region",
      "operator": "eq",
      "value": "华东",
      "values": []
    },
    {
      "field": "order_year",
      "operator": "eq",
      "value": "2025",
      "values": []
    }
  ],
  "order_by": [
    {"field": "sales_amount", "direction": "desc"}
  ],
  "limit": 3,
  "comparison": null
}
```

#### 子模型

- `FilterSpec`：过滤字段、操作符、单值和多值；
- `OrderSpec`：排序字段和方向；
- `ComparisonSpec`：同比或环比；
- `QuerySpec`：指标、维度、过滤、排序、限制和对比设置；
- `CompiledQuery`：编译后的 SQL 与参数元组。

#### 已支持过滤操作

```text
eq / neq / contains / in / gte / lte / between / year
calendar_month / calendar_quarter / month_range
this_year / last_year / this_month / last_month
this_quarter / last_quarter / last_n_days / last_n_months
```

#### 编译过程

1. 从业务字典读取指标聚合表达式和维度表达式。
2. 根据指标、维度和过滤条件收集需要的表。
3. 按固定 JOIN 映射添加关联。
4. 编译过滤条件并生成参数列表。
5. 对默认业务指标自动增加 `PAID` 状态条件。
6. 生成 `GROUP BY`、`ORDER BY` 和 `LIMIT`。
7. 同比/环比使用两个 CTE 计算本期、对比期和增长率。

模型无法自由提供 SQL 标识符。`query_spec_json_schema()` 会从业务字典动态生成指标和维度 ID 枚举。

#### 当前编译限制

- 表别名和 JOIN 图仍固定为当前电商结构；
- 复杂子查询、窗口函数和多层聚合尚未支持；
- 对比查询只支持单指标且不带分组；
- 部分不合理粒度组合会被主动拒绝，例如按商品统计订单粒度金额；
- SQL 编译器还没有根据任意 Catalog 自动构造 JOIN。

### 4.5 `app/value_resolver.py`：实体值解析

该模块处理模型已经判断出“过滤哪个业务维度”之后，用户输入值与数据库真实值不完全一致的问题。

当前允许解析的字段由后端白名单 `VALUE_SOURCES` 决定：

```text
product / category / region / customer / payment_method / order_status
```

处理步骤：

1. 从白名单表和列读取去重候选值；
2. 使用 Unicode NFKC、大小写、标点和下划线归一化；
3. 处理“15号测试商品”与“测试商品_15”这样的编号顺序差异；
4. 优先应用业务字典中的 `value_aliases`；
5. 对候选值计算相似度；
6. 高置信度且领先第二名时自动修正；
7. 多个候选接近时抛出 `NeedsClarification`；
8. 完全找不到时返回查询错误。

阈值逻辑：

- 完全匹配分数为 1.0；
- 模糊匹配至少 0.86，且领先第二名至少 0.08 时自动选择；
- 否则返回最多 5 个分数不低于 0.35 的候选。

候选值读取函数带 LRU 缓存，避免每次查询重复扫描字段。数据库数据更新后，应调用 `clear_candidate_cache()`；当前数据源扫描接口只清除了 Catalog 检索缓存，尚未主动清除此候选缓存，这是后续可以补强的点。

### 4.6 `app/database.py`：数据库访问

提供三个函数：

- `check_database()`：测试默认数据库连接；
- `execute_readonly()`：执行参数化只读查询；
- `fetch_distinct_text_values()`：从后端白名单字段读取实体候选。

`execute_readonly()` 的保护措施：

- 开启 PostgreSQL 事务；
- 执行 `SET TRANSACTION READ ONLY`；
- 设置 `statement_timeout`；
- 使用 SQL 参数，不拼接用户值；
- 最多读取 `MAX_RESULT_ROWS` 行。

`fetch_distinct_text_values()` 使用 `psycopg.sql.Identifier` 构造标识符，而且表名和列名只来自代码白名单，不接受用户直接输入。

### 4.7 `app/sql_guard.py`：SQL 安全检查

使用 SQLGlot 将 SQL 解析为 PostgreSQL AST。

当前检查包括：

- 必须只包含一条语句；
- 根节点必须是查询；
- 拒绝 INSERT、UPDATE、DELETE、DROP、ALTER、CREATE 等写操作；
- 拒绝事务控制和行锁相关节点；
- 解析后重新输出规范化 PostgreSQL SQL。

安全由两层共同保证：SQL AST 校验负责应用层检查，PostgreSQL 只读事务负责数据库层兜底。

### 4.8 `app/catalog_models.py`：统一元数据契约

该模块使用 Pydantic 定义版本化的统一目录模型：

- `SourceInfo`：数据源基本信息；
- `ColumnMeta`：字段名、类型、可空性、说明、敏感等级、样例值；
- `ForeignKeyMeta`：主外键关系；
- `IndexMeta`：索引；
- `TableMeta`：表信息、字段、主键、外键和索引；
- `UnifiedCatalog`：完整数据源目录；
- `RagDocument`：供检索使用的文档；
- `TableOverride`、`ColumnOverride`、`CatalogOverrides`：人工业务语义覆盖。

所有模型默认禁止未知字段，防止格式漂移。当前目录版本为 `1.0`。

### 4.9 `app/catalog_builder.py`：数据库扫描与目录生成

主要能力：

- 将 PostgreSQL 原生类型归一化为 string、integer、decimal、date、datetime 等类型；
- 从系统目录读取普通表、字段、注释、主键、外键和索引；
- 整个扫描过程使用只读事务；
- 可选读取低基数字段值；
- 应用 `catalog/overrides.json` 中的中文业务说明和敏感等级；
- 将统一 Catalog 转换成 table、column、relationship、metric、dimension、value_dictionary 六类检索文档；
- 输出 Catalog、JSON Schema 和 JSONL 检索文档。

低基数采样会多取一个值：如果真实不同值数量超过阈值，则完全不保存该字段的候选值。敏感等级不是 `normal` 的字段在应用覆盖信息后会清除样例值。

生成文件：

```text
catalog/generated/catalog.json
catalog/generated/catalog.schema.json
catalog/generated/rag_documents.jsonl
catalog/generated/overrides.schema.json
```

### 4.10 `app/catalog_retrieval.py`：本地检索

当前实现不是向量数据库，也不调用 Embedding API，而是轻量本地混合文本检索。

主要评分信号：

- 中文双字切分和英文/数字 token；
- BM25 风格词频评分；
- 字符二元组重叠；
- 业务别名命中加权。

支持按以下条件过滤：

- 文档类型；
- `source_id`；
- 返回数量。

该实现适合当前小型目录，成本为零、部署简单。数据库和文档规模扩大后，应替换为关键词倒排索引与向量检索组合，同时保留 `RagDocument` 契约。

### 4.11 `app/source_registry.py`：数据源登记

读取 `catalog/sources.json`，并提供：

- 数据源 ID 唯一性校验；
- PostgreSQL 类型限制；
- 连接环境变量名称校验；
- 项目目录越界保护；
- 公共状态输出；
- 数据库连接测试；
- 扫描、覆盖、业务字典合并和目录写入。

登记文件只保存连接环境变量名称，例如 `DATABASE_URL`，不保存真实连接串或密码。

需要特别注意：数据源登记与扫描已经支持 `source_id`，但当前 `/query` 执行仍使用 `settings.database_url`，`llm.py` 和 `query_spec.py` 也仍使用全局业务字典。因此现在属于“多数据源管理基础”，尚未完成真正的多数据源问数。

## 5. 前端模块说明

前端未使用 React、Vue 等框架，采用原生 HTML、CSS 和 JavaScript，适合当前原型阶段。

### 5.1 `static/index.html`：问数主页面

页面结构包括：

- 左侧品牌、示例问题、最近查询和服务状态；
- 顶部项目说明和数据源管理/API 文档入口；
- 自然语言问题输入框；
- 加载状态；
- 查询结果区域；
- 表格、图表和 SQL 三个标签页；
- 错误与候选澄清区域。

### 5.2 `static/app.js`：问数交互

主要功能：

- 检查 `/health` 并显示数据库连接状态；
- 提交问题到 `/query`；
- 处理正常结果、普通错误和 HTTP 409 澄清响应；
- 用户选择候选后调用 `/query/resolve`；
- 动态渲染结果表格；
- 根据结果自动绘制简单横向柱状图；
- 对同比/环比结果显示增长率卡片；
- 显示模型生成计划最终编译出的 SQL；
- 将最近 8 个问题保存在浏览器 `localStorage`；
- 支持 Ctrl+Enter 提交和复制 SQL。

安全方面，页面输出使用 `escapeHtml()` 转义，减少把数据库文本直接插入 HTML 所造成的风险。

当前图表判断较简单：选择结果中的第一个数字字段作为数值、第一个其他字段作为标签，只显示前 12 行。它还不能根据查询语义准确决定折线图、柱状图或饼图。

### 5.3 `static/styles.css` 与 `resolver.css`

- `styles.css` 负责整体布局、侧边栏、查询卡片、表格、标签页、图表、加载和错误状态；
- `resolver.css` 负责候选澄清按钮和自动匹配提示。

目前两个主 CSS/JS 文件经过压缩式书写，可运行但维护性一般。后续扩展前端时建议重新格式化，并按页面区域拆分。

### 5.4 `static/admin.html` 与 `admin.js`

数据源管理页面提供：

- 读取 `/sources` 并展示数据源卡片；
- 显示连接是否配置、目录是否生成、表数量和文档数量；
- 调用连接测试接口；
- 调用只读扫描接口；
- 选择是否扫描低基数字段候选；
- 调用 `/catalog/search` 预览目录检索结果。

当前页面只能操作 `catalog/sources.json` 中已经存在的数据源，不能在网页中新增、编辑或删除数据源。

## 6. 数据库设计

当前演示库包含 5 张表。

| 表 | 作用 | 关键关系 |
|---|---|---|
| `customers` | 客户及客户地区 | `id` 被订单引用 |
| `products` | 商品及类别 | `id` 被订单明细引用 |
| `orders` | 订单头、日期、状态、总额 | 关联客户 |
| `order_items` | 商品数量和成交单价 | 关联订单与商品 |
| `payments` | 支付日期和支付方式 | 关联订单 |

核心关系：

```text
customers 1 ── N orders 1 ── N order_items N ── 1 products
                       │
                       └──── payments
```

业务口径：

- 销售额在订单明细粒度计算：`SUM(quantity * unit_price)`；
- 订单总额在订单头粒度计算：`SUM(orders.total_amount)`；
- 大部分业务指标默认只统计 `PAID`；
- 地区默认表示客户地区；
- 金额单位为元。

### 6.1 数据脚本

- `db/schema.sql`：创建表、约束和索引；
- `db/seed.sql`：清空五张演示表并生成 200 个客户、30 个商品和 2000 个随机订单；
- `db/seed_scenarios.sql`：使用高位 ID 增加边界场景数据，可重复执行，不清空基础随机数据。

场景数据包含相似商品名、带“地区”的学校客户名、零销量商品、取消/退款订单和跨年度订单，用于验证实体识别和时间逻辑。

## 7. 业务字典

`business_dictionary.json` 是模型理解与后端执行之间的语义契约。

每个指标可包含：

- `label`：中文名称；
- `expression`：由后端使用的 SQL 表达式；
- `tables`：依赖表；
- `default_paid_only`：是否默认只统计已支付订单；
- `unit`：单位；
- `synonyms`：自然语言同义词。

每个维度可包含：

- `label`；
- `expression`；
- `tables`；
- `value_type`；
- `synonyms`；
- `retrieval_terms`；
- `value_aliases`。

业务字典同时驱动：

1. 模型 Prompt 中的业务目录；
2. QuerySpec JSON Schema 枚举；
3. QuerySpec 后端校验；
4. SQL 表达式和依赖表；
5. Catalog 指标/维度检索文档；
6. 实体值别名转换。

因此修改业务字典后需要重启应用；如果修改了检索词，还应重新生成 Catalog。

## 8. 完整运行流程示例

问题：

```text
查询2025年15号测试商品销售额
```

### 第一步：本地目录检索

检索得到可能相关的：

```text
metric: sales_amount
dimension: product
dimension: order_year / order_date
```

### 第二步：模型生成 QuerySpec

```json
{
  "metrics": ["sales_amount"],
  "dimensions": [],
  "filters": [
    {"field": "product", "operator": "eq", "value": "15号测试商品", "values": []},
    {"field": "order_year", "operator": "eq", "value": "2025", "values": []}
  ],
  "order_by": [],
  "limit": null,
  "comparison": null
}
```

### 第三步：实体解析

后端从 `products.name` 加载候选，识别：

```text
15号测试商品 → 测试商品_15
```

### 第四步：后端编译

生成的 SQL 使用占位符：

```sql
SELECT SUM(oi.quantity * oi.unit_price) AS "销售额"
FROM orders o
JOIN order_items oi ON oi.order_id = o.id
JOIN products p ON p.id = oi.product_id
WHERE p.name = %s
  AND EXTRACT(YEAR FROM o.created_at)::int = %s
  AND o.status = %s
LIMIT 200
```

参数单独传递：

```text
测试商品_15, 2025, PAID
```

### 第五步：安全校验与执行

SQLGlot 确认它是单条只读查询，随后 PostgreSQL 在只读事务中执行。

### 第六步：前端显示

响应中包含结果、SQL、参数和自动匹配说明。前端展示“15号测试商品 → 测试商品_15”。

## 9. 候选澄清流程

问题：

```text
2025年极光键盘的销售额
```

数据库中存在：

```text
极光键盘-标准版
极光键盘-专业版
```

后端不会直接选择，而是抛出 `NeedsClarification`。接口返回 HTTP 409 和候选列表。前端生成候选按钮；用户点击后，把原 QuerySpec、过滤条件序号和选中值发送到 `/query/resolve`。后端替换该值并继续编译执行，不再消耗模型 API。

## 10. 数据源扫描流程

```text
catalog/sources.json
→ SourceRegistry 读取数据源定义
→ 从 connection_env 获取真实连接串
→ PostgreSQL 只读扫描
→ 应用 catalog/overrides.json
→ 合并 business_dictionary.json
→ 生成统一 Catalog 与 RAG 文档
→ 清除 CatalogRetriever 缓存
```

连接串不会写入 Catalog 或返回管理页面。

## 11. 测试与评测体系

### 11.1 单元测试

| 文件 | 覆盖内容 |
|---|---|
| `test_query_spec.py` | QuerySpec 验证、JOIN、参数化和粒度保护 |
| `test_time_filters.py` | 相对时间、月份、季度、范围 |
| `test_comparisons.py` | 同比和环比 SQL |
| `test_value_resolver.py` | 实体匹配、别名、澄清和本地实体提示 |
| `test_sql_guard.py` | 写操作、多语句和行锁拒绝 |
| `test_catalog_builder.py` | 类型归一化、目录文档、覆盖和敏感值 |
| `test_catalog_retrieval.py` | 本地检索、别名和 Prompt 缩减 |
| `test_source_registry.py` | 数据源格式、路径、环境变量和密码隐藏 |

当前记录：89 项单元测试通过。

### 11.2 数据集

- `eval/cases.json`：60 道基础执行评测；
- `eval/advanced_cases.json`：75 道中高难度 QuerySpec/检索评测；
- `eval/scenario_cases.json`：14 道配合场景数据的端到端测试。

### 11.3 评测程序

- `run_eval.py`：通过真实 `/query` 接口提交问题，并与 gold SQL 执行结果比较；
- `run_plan_eval.py`：少量调用模型，比较 QuerySpec；
- `run_retrieval_eval.py`：纯本地检索召回率；
- `validate_gold.py`：检查测试集 gold SQL 是否可执行；
- `validate_advanced_cases.py`：检查高级测试集格式；
- `rescore_*.py`：在不重复调用模型的情况下重评分；
- `analyze_prompt_reduction.py`：分析检索模式对 Prompt 长度的缩减效果。

当前记录：

- 高级检索 Recall@8：65/65，100%；
- 场景题 14/14 均获得通过记录；
- 场景调试过程总计调用模型 22 次。

## 12. 辅助脚本

### `scripts/build_catalog.py`

从命令行扫描 PostgreSQL 并生成 Catalog。支持设置数据源 ID、名称、输出目录、是否采样候选值和最大基数。

### `scripts/load_scenario_data.py`

读取 `.env` 中的默认数据库连接，执行 `db/seed_scenarios.sql`，然后输出新增场景记录数量。

## 13. 数据与模型边界

### V2 默认会发送给千问

- 用户自己输入的问题；
- 当前日期；
- 检索后的指标、维度名称、同义词和业务规则；
- QuerySpec JSON Schema。

### V2 默认不会发送给千问

- 数据库密码和连接串；
- SQL 查询结果；
- 整张数据库表；
- 实体候选值全集；
- PostgreSQL 系统信息；
- 最终由后端生成的 SQL。

V1 会发送完整 Schema 和业务字典，因此仅用于基线实验，不应作为未来默认方案。

## 14. 当前已知问题

### 14.1 多数据源尚未闭环

`SourceRegistry` 可以登记和扫描多个数据源，`CatalogRetriever.search()` 也支持 `source_id`，但：

- `/query` 请求没有 `source_id`；
- `database.py` 固定使用默认 `DATABASE_URL`；
- `query_spec.py` 在导入时读取全局业务字典；
- `llm.py` 默认读取一个生成目录；
- 前端没有数据源选择器。

因此新增第二个数据库后，目前只能扫描，不能完整问数。

### 14.2 SQL 编译器与电商结构绑定

`JOIN_SQL`、表别名和业务表达式对应当前五张表。要兼容学校、医疗或制造数据库，需要将这些内容移动到每个数据源自己的语义层，并让编译器根据关系图生成 JOIN。

### 14.3 当前检索不是真正的大规模 RAG

当前是内存中的 BM25 风格检索。它适合几十到几千条目录文档，但不适合百万字段值或大量数据源。后续应增加持久化倒排索引、向量索引和按数据源隔离的混合召回。

### 14.4 实体候选依赖字段全量去重

当前首次解析某个字段时会查询最多 1000 个不同值并缓存。对于高基数字段，需要改成数据库索引搜索、三元组索引、全文检索或独立实体索引。

### 14.5 前端能力仍基础

- 图表类型由简单规则判断；
- 没有自然语言结果总结；
- 历史记录只存在浏览器；
- 没有数据源切换；
- 没有会话级多轮上下文；
- 管理页不能新增数据源。

### 14.6 没有自动修复循环

模型计划、实体解析或 SQL 执行失败后，系统直接返回错误或澄清，不会把错误重新交给模型。当前这样做更省 API 成本，也更容易控制安全边界；复杂修复可以在后期以限定次数加入。

## 15. 下一版本建议

推荐 V0.6 目标：用第二个完全不同的 PostgreSQL 数据库验证通用性。

建议开发顺序：

1. 给 `QueryRequest`、`CatalogSearchRequest` 增加 `source_id`；
2. 前端增加数据源选择器；
3. `SourceRegistry` 根据 `source_id` 返回连接串、目录和业务字典；
4. 将全局 `DICTIONARY` 改为按数据源加载；
5. 将 CatalogRetriever 缓存改成按数据源保存；
6. 让数据库执行函数显式接收连接串；
7. 为每个数据源建立独立的 QuerySpec 枚举；
8. 将 JOIN 关系和表达式从 Python 常量迁移到语义配置；
9. 建立学校测试数据库验证整个闭环；
10. 再考虑用模型一次性生成业务字典草稿。

建议学校库表：

```text
schools / students / majors / enrollments / scores
```

验收问题：

```text
2025年华东地区招生人数最多的三所学校
计算机专业平均成绩是多少
名称包含实验学校的主体有哪些
华东实验学校和华东第一实验学校的招生人数
```

## 16. 常用命令

```powershell
# 安装
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 初始化场景数据
python -m scripts.load_scenario_data

# 生成目录
python -m scripts.build_catalog --source-id analytics_local --include-value-samples

# 启动
uvicorn app.main:app --reload --port 8000

# 单元测试
pytest -q

# 验证场景 gold SQL
python -m eval.validate_gold --case-file scenario_cases.json

# 端到端场景评测，会调用模型
python eval/run_eval.py --case-file scenario_cases.json --split all

# 本地目录检索评测，不调用模型
python -m eval.run_retrieval_eval
```

## 17. 总结

当前项目已经完成“自然语言 → 结构化查询计划 → 后端参数化 SQL → 只读执行 → 网页结果”的最小安全闭环，并建立了业务字典、实体解析、目录扫描、本地检索和自动评测基础。

它目前仍是针对单个电商 PostgreSQL 数据库的原型，而不是已经完成的通用多数据库平台。下一阶段最关键的工作不是继续堆提示词，而是把连接、业务字典、Catalog、检索器和 SQL 编译上下文全部改为按 `source_id` 隔离，并用第二种数据库结构验证通用性。
