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
