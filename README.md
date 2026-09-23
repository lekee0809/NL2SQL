# NL2SQL：中文智能问数

一个面向 PostgreSQL 的中文自然语言查询原型。用户在网页中输入业务问题，千问负责生成受约束的结构化查询计划，后端根据业务字典编译参数化 SQL，经过只读校验后执行并展示表格、图表和 SQL。

当前版本：**V0.9**

完整的前后端模块、执行流程和扩展说明见 [`docs/CODE_REPORT.md`](docs/CODE_REPORT.md)。

## 核心思路

默认链路不让模型直接拼写数据库字段或 SQL：

```text
中文问题
  ↓
本地目录检索 + 实体类型提示
  ↓
千问生成 QuerySpec（结构化 JSON）
  ↓
后端按业务字典编译参数化 SQL
  ↓
AST 只读校验 + PostgreSQL 只读事务
  ↓
表格 / 图表 / SQL
```

项目仍保留 `/query/v1` 作为“模型直接生成 SQL”的实验基线；网页和 `/query` 默认使用安全性更高的 V2 QuerySpec 链路。

## 已实现能力

- 中文指标、维度、过滤、排序和 Top-K 查询
- 指定年份、月份、季度、连续月份和相对时间
- 单指标同比、环比
- 商品、客户、地区、类别、支付方式和订单状态解析
- 实体名称规范化、模糊匹配和候选澄清
- 本地 Schema/业务目录检索，减少 Prompt 长度
- PostgreSQL 数据源连接测试与只读扫描
- 统一元数据目录和 RAG 文档生成
- 参数化 SQL、AST 校验、只读事务、超时和返回行数限制
- 网页多轮问数、候选值确认、会话恢复；按查询维度自动选择指标卡、趋势折线、分类比较或占比图，并可手动切换指标与图表，始终保留结果表格和 SQL
- 自动评测、检索评测和可重复导入的边界场景数据
- 基于 QuerySpecPatch 的多轮续问、条件继承和候选澄清 API
- 商品和客户的多对多实体别名；唯一命中时绑定稳定实体 ID，重名时要求确认，续问保留该 ID

## 技术栈

- Python 3.11+
- FastAPI、Pydantic、SQLGlot、psycopg 3
- PostgreSQL
- 阿里云百炼 DashScope OpenAI 兼容接口
- 原生 HTML、CSS、JavaScript

## 项目结构

```text
app/                         FastAPI、查询计划、SQL 编译与安全校验
catalog/                     数据源登记、元数据覆盖与生成目录
db/schema.sql                PostgreSQL 表结构
db/seed.sql                  基础随机测试数据
db/seed_scenarios.sql        可重复导入的边界场景数据
eval/                        测试集、评测程序与报告
scripts/                     数据导入和目录生成脚本
static/                      问数页面与数据源管理页面
tests/                       自动化单元测试
business_dictionary.json     指标、维度、别名和业务规则
```

## 快速开始

### 1. 准备 PostgreSQL

创建一个名为 `analytics` 的数据库，然后依次执行：

```text
db/schema.sql
db/seed.sql
```

如果要运行边界场景测试，再执行 `db/seed_scenarios.sql`。该文件使用独立的高位 ID，重复执行只替换自己的场景记录，不会清空基础随机数据。

也可以在配置好环境后运行：

```powershell
python -m scripts.load_scenario_data
```

### 2. 安装依赖

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. 配置环境变量

```powershell
Copy-Item .env.example .env
```

编辑 `.env`：

```dotenv
DATABASE_URL=postgresql://postgres:你的密码@127.0.0.1:5432/analytics
DASHSCOPE_API_KEY=你的百炼APIKey
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen3.8-flash
CATALOG_RETRIEVAL_ENABLED=true
CATALOG_RETRIEVAL_TOP_K=10
SESSION_TTL_SECONDS=3600
DEFAULT_SOURCE_ID=analytics_local
```

`.env` 已被 Git 忽略，不要把数据库密码或 API Key 写入其他配置文件。

### 4. 生成统一数据目录

```powershell
python -m scripts.build_catalog --source-id analytics_local --include-value-samples --max-sample-values 20
```

默认只扫描数据库结构。启用候选值采样时，仅保留不超过阈值的低基数字段值；标记为敏感的字段不会写入样例。

### 5. 启动服务

```powershell
uvicorn app.main:app --reload --port 8000
```

打开：

- 问数页面：<http://127.0.0.1:8000/>
- 数据源管理：<http://127.0.0.1:8000/admin>
- API 文档：<http://127.0.0.1:8000/docs>

## 主要接口

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/health` | 检查 API、数据库和目录检索状态 |
| `POST` | `/query` | 默认 QuerySpec 安全查询链路 |
| `POST` | `/query/resolve` | 选择候选实体后继续执行，不再次调用模型 |
| `POST` | `/query/v1` | 模型直接生成 SQL 的实验基线 |
| `POST` | `/sessions` | 创建多轮查询并执行首轮问题 |
| `POST` | `/sessions/{id}/query` | 基于上一轮 QuerySpec 执行增量续问 |
| `POST` | `/sessions/{id}/resolve` | 确认多轮会话中的候选实体，不调用模型 |
| `GET` | `/sessions/{id}` | 查看当前会话状态 |
| `DELETE` | `/sessions/{id}` | 删除当前会话 |
| `POST` | `/catalog/search` | 本地目录检索预览 |
| `GET` | `/sources` | 查看数据源状态 |
| `POST` | `/sources/{id}/test` | 测试数据源连接 |
| `POST` | `/sources/{id}/scan` | 只读扫描并重建目录 |

查询示例：

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/query `
  -ContentType application/json `
  -Body '{"question":"2025年华东地区销售额最高的三个商品"}'
```

多轮查询示例：

```powershell
$first = Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/sessions `
  -ContentType application/json `
  -Body '{"question":"2025年华东地区销售额最高的商品"}'

Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/sessions/$($first.session_id)/query" `
  -ContentType application/json `
  -Body '{"question":"改成华南，只看前五个"}'
```

多轮链路会先在本地解析地区、年份、Top-K、分组、排序、指标切换和条件删除等简单续问，不消耗模型 token；只有本地无法完整理解时，才把当前 QuerySpec、最新消息和检索后的业务目录发给模型生成增量 QuerySpecPatch。未提及的条件由后端确定性保留，响应中的 `patch_source` 会标明使用了 `local` 还是 `model`。当前会话保存在进程内存中，默认闲置 3600 秒后自动清理，服务重启后也会清空；可通过 `SESSION_TTL_SECONDS` 调整，后续可替换为 Redis。

模型查询响应包含 `model_call`：模型名称、实际输入/输出 token、耗时和缓存命中状态。相同模型、提示词、目录和问题在进程内复用已验证的 QuerySpec，缓存最多保留 256 条、有效期 10 分钟；服务重启会清空。运行日志只记录模型、用量和耗时，不记录问题内容或数据库值。明确的地区名称若被模型误写为 `contains`，执行前会转为 `eq` 并在响应的 `normalizations` 中说明原因；明确要求模糊匹配时保留 `contains`。

## 安全边界

- 模型只能输出符合 JSON Schema 的 QuerySpec。
- 指标和维度 ID 必须来自业务字典白名单。
- 表、字段、JOIN、聚合表达式和最终 SQL 由后端生成。
- 用户值通过数据库参数传递，不拼接进 SQL。
- SQL 必须是单条 `SELECT` 或 `WITH`，并经过 AST 检查。
- 查询在 PostgreSQL 只读事务中执行，并限制超时和最大返回行数。
- 数据库连接串只从环境变量读取，不通过管理页面返回。
- 数据写入仅存在于独立初始化/测试脚本，问数接口没有写能力。

## 测试与评测

```powershell
# 单元测试
pytest -q

# 网页多轮交互测试（需要 Node.js，不调用模型）
node tests/test_frontend.cjs

# 本地检索评测，不调用模型
python -m eval.run_retrieval_eval

# 只读性能基准：放大目录检索文档，并测当前库候选值加载；不调用模型
python -m eval.benchmark_catalog_search --multiplier 100 --repeats 10

# 100 万条临时别名 + 1000 次查找；不修改正式数据、不调用模型
python -m eval.benchmark_entity_aliases --aliases 1000000 --lookups 1000

# 14 道端到端场景题，会调用模型
python eval/run_eval.py --case-file scenario_cases.json --split all
```

当前验证记录：

- 单元测试：183 项通过；网页多轮交互测试通过
- 多轮固定场景：84 个续问回合，其中 79 个由本地规则零 token 处理
- 多轮真实验证：本地 Patch 与模型兜底均已完成只读数据库执行
- 高级目录检索 Recall@8：65/65（100%）
- 新增端到端场景：14/14 均获得通过记录

详细说明见 [`eval/README.md`](eval/README.md)，历史报告位于 `eval/reports/`。

## 数据目录与扩展

`catalog/generated/catalog.json` 是版本化统一物理元数据；`rag_documents.jsonl` 将表、字段、关系、指标、维度和低基数值转换为统一检索文档。未来接入 MySQL、SQL Server 等数据库时，只需新增元数据适配器并输出相同目录契约，上层检索与 QuerySpec 无需整体重写。

检索器会在加载目录时预计算文档字符片段，避免每次查询重复处理。性能基准里的文档放大只用于观察算法耗时，不代表已经验证真实大库；当前候选值仍最多读取 1000 个不同值。`db/schema.sql` 提供商品和客户名称的普通索引定义，但不会自动修改现有数据库。

### 商品和客户别名

现有 PostgreSQL 库先运行 `python -m scripts.apply_entity_alias_schema` 查看目标，再用 `--apply` 安装增量表；新建库执行 `db/schema.sql` 时会自动包含该表。别名只在本地数据库检索，不会把整份别名库放进模型提示词。别名表允许同一个实体有多个昵称、同一个昵称指向多个实体；后者会要求用户选择。确认后 QuerySpec 使用商品或客户 ID 编译 SQL，后续只改时间、指标等条件时会保留该 ID。

准备 UTF-8 CSV（列名固定为 `source_id,entity_type,entity_id,alias`），例如：

```csv
source_id,entity_type,entity_id,alias
analytics_local,product,37,办公室键盘
analytics_local,customer,12,老客户甲
```

先运行 `python -m scripts.import_entity_aliases --file aliases.csv` 验证实体 ID，再加 `--apply` 批量导入；重复导入会跳过已有映射。CSV 中的 ID 只是格式示例，实际导入前必须换成数据库中存在的 ID。当前只支持默认 PostgreSQL 数据源，且若昵称与正式名称毫无关联，提问时最好明确写“商品名为……/客户名为……”，让模型能正确选中实体字段。未匹配到别名时仍按原有名称和模糊候选流程处理；高基数实体的普通名称匹配仍有最多 1000 个候选值的限制。

当前数据源登记位于 `catalog/sources.json`。每个数据源只保存连接环境变量名称，不保存真实密码。

## 下一步

- 在管理页面登记和切换多个数据源
- 为大型数据库接入向量检索和混合 RAG
- 扩展多指标、子查询和窗口分析
- 增加自然语言结果总结和更丰富的图表
- 在确有必要时加入有限的 SQL 修复循环与多轮澄清

当前版本暂不使用 MCP，也不要求 Docker。
