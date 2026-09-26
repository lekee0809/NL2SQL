# 评测说明

`cases.json` 包含 60 道测试题：dev 40、validation 10、test 10。三道安全题要求接口拒绝，其余题目通过执行结果与 gold SQL 对比。报告同时给出严格准确率和语义准确率；语义准确率允许结果包含额外辅助列。

`advanced_cases.json` 额外包含 75 道中高难度题：dev 60、validation 15，不增加或改动原有 test。每题记录预期 QuerySpec 或澄清/拒绝行为，并包含指标和维度的检索目标。两份合计 135 题。

`scenario_cases.json` 是与 `db/seed_scenarios.sql` 配套的 14 道端到端场景题，重点覆盖相似实体名、客户名称中的“地区”歧义、连续月份、季度同比、候选澄清和不存在实体。场景数据使用独立高位 ID，可重复导入且不会清空原始随机数据。

先导入场景数据，再运行端到端评测：

```powershell
python -m scripts.load_scenario_data
python eval/run_eval.py --case-file scenario_cases.json --split all
```

先启动 FastAPI，再运行：

```powershell
$env:PYTHONPATH=(Resolve-Path '.packages').Path
python eval/run_eval.py --split dev
python eval/run_eval.py --split validation
python eval/run_eval.py --split test
python eval/run_eval.py --case-file scenario_cases.json --split all
```

开发阶段只根据 dev 和 validation 修改 Prompt 或业务字典。test 是最终保留集，不应反复用于调参。

高级集合的结构验证与纯本地检索评测：

```powershell
python -m eval.validate_advanced_cases
python -m eval.run_retrieval_eval
```

上述命令不会调用模型。检索报告写入 `eval/reports/retrieval-advanced-latest.json`。

少量检索模式 QuerySpec 抽查使用 `python -m eval.run_plan_eval --id ...`。它会调用模型，只应传入少量代表题；已有报告可通过 `python -m eval.rescore_plan_report --report 报告名.json` 在修正标注后离线重评分。

评测器默认最多调用模型 5 次，超过会直接拒绝；可在 1～10 范围内显式调整。针对单个回归问题时建议写入独立报告，避免覆盖已有抽样结果：

```powershell
python -m eval.run_plan_eval --id advanced_001 --max-api-calls 1 --output regression-advanced-001.json
```

多轮联网冒烟测试固定包含 3 个首轮问题、2 个复杂模型续问和 2 个本地续问，模型调用硬上限为 5：

```powershell
python -m eval.run_conversation_live_eval --max-api-calls 5
```

最近一次结果：5 次模型调用，3373 input token、619 output token、3992 total token；严格通过 6/7。唯一失败是明确地区被生成为 `contains` 而非 `eq`，已加入提示词回归规则，未为此追加联网调用。

## 隔离大库验证（2026-09-27）

独立 PostgreSQL 库 `nl2sql_scale_20260927` 包含 50 万订单、100 万订单明细、40 万支付记录、20 万实体别名；不修改默认 `analytics`。合成数据覆盖 2023–2026 年、5 个地区、4 个商品类别。全量检查确认订单金额与明细一致、支付记录只对应已支付订单、200,002 条别名检索键与规范化文本一致。

- 第一次零模型评测 27/30：发现高编号商品精确名称在读取前 1000 个候选值之后才尝试匹配，已改为先走索引精确查询。
- 最终零模型评测 33/33：9 种查询各重复 3 次，另含多轮本地续问、歧义澄清、相对时间修复、目录扫描及两种连接模式各 200 次别名查找。查询中位数 141 ms、P95 446 ms；带采样目录扫描约 1.74 秒；别名查找 P95 为复用连接 0.269 ms、逐次新建连接 142.8 ms。
- 首轮端到端模型测试 3/5：发现相对时间被偶发写成非数字年份，以及并列排行榜未稳定排序。两处均已修复。最终复测 5/5（4 次模型调用、1 次零 token 续问），最终一轮模型用量 3,482 token。

逐轮报告：`scale-20260927-run1.json` 至 `run4.json`，以及 `scale-live-20260927-run1.json`、`scale-live-20260927-diagnostic.json`、`scale-live-20260927-run2.json`、`scale-live-20260927-monthly-fix.json`、`scale-live-20260927-final.json`。只将最终通过的两份合成数据报告纳入版本记录，其余诊断报告留在本机。注意：查询仍有 5 秒默认超时、最多返回 200 行；合成分布和单机 PostgreSQL 不能替代真实业务数据及异构数据库验证。
