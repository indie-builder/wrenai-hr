# 验证入口

本目录维护固定 SQL 双路径回归、自然语言评测和验证工具测试。以下命令从**仓库根目录**执行；环境和 `hr_demo_duck` profile 初始化见 [演示 README](../../README.md#环境与首次初始化)。本页说明当前操作，日期化的验收记录见 [matrix_v2.md](matrix_v2.md)。

## 固定 SQL 回归

[questions.py](questions.py) 是题库与计算口径的入口；[run_all.py](run_all.py) 负责执行和比较：

- A 路径：题库 `gt` SQL 直连物理表，经根 `.venv` 中的 DuckDB 子进程只读挂载 `hr-demo/db/duckdb/public.duckdb`；runner 本体不依赖 DuckDB 模块。
- B 路径：题库 `wren` SQL 经根 `.venv/bin/wren` 执行，使用语义项目当前绑定的 profile。
- 两路相等证明相应固定 SQL 执行结果一致；业务定义需独立核对，不代表自然语言生成准确率。

```bash
# 子集默认写 runs/<题号组合>/，保留完整汇总
python3 hr-demo/validation/v2/run_all.py --only q03
python3 hr-demo/validation/v2/run_all.py --domain 人效分析
# 完整回归更新 summary.csv 和 results/qXX.{gt,wren}.csv
python3 hr-demo/validation/v2/run_all.py
# 需要独立保存全量证据时，指定报告目录
python3 hr-demo/validation/v2/run_all.py --output-dir hr-demo/validation/v2/runs/local-check
```

以当次退出码、汇总和明细判断结果。失败时移除对应旧 CSV 并保留执行状态，退出码非零；连接异常原文不写入可提交报告。runner 检查返回码、超时、完整 CSV/JSON 和有限数值，执行错误不作为数据。

题目声明需要的输出与比较条件：数值默认绝对容差 `0.011`，可按题设置 `tolerance`；排名/趋势题设置 `ordered=True`，逐行核对顺序；合法空结果需设置 `allow_empty=True`。此容差不是所有财务业务的精度要求，正确业务口径优先于测试通过。

## 按改动选择检查

```bash
# 验证程序、数据构建与仪表盘导出工具的单元及集成测试
.venv/bin/python -m unittest discover -s hr-demo/validation/v2/tests -v
# YAML/规则静态检查，以及隔离构建与 target 的一致性
.venv/bin/python hr-demo/validation/v2/check_semantics.py --build-check
# 页面快照哈希、源数据和全部页面查询的一致性
.venv/bin/python hr-demo/scripts/export_dashboard.py --check
```

共享模型、关系、指标口径变更执行全量 SQL 回归；局部修改回归受影响问题。MCP 与共享 SQL 执行的测试在根 `tests/`，按 [MCP 文档](../../docs/mcp-vercel.md#本地运行) 执行；文档修改按 [文档检查](../../README.md#文档检查) 核对，无需重新构库或全量 SQL 回归。

## 自然语言评测

[nl_eval.py](eval/nl_eval.py) 不调用模型：先导出无答案题包，由独立 Agent 只读题包生成 JSONL，再由评测器执行和比较。生成端不能读取标准答案 SQL、历史结果或数据库；仅公开输出列与展示精度。保留真实输入、原始生成 SQL、结果及失败记录。

```bash
# 使用新的输出目录；可加 --only q03 q18 筛题
.venv/bin/python hr-demo/validation/v2/eval/nl_eval.py export \
  --output-dir /tmp/hr-nl-package
# 独立 Agent 按 protocol.json 生成记录后执行
.venv/bin/python hr-demo/validation/v2/eval/nl_eval.py run \
  --records /tmp/hr-generated.jsonl \
  --output-dir hr-demo/validation/v2/runs/nl-first
```

JSONL 必填 `id`、原始 `question`、`generated_sql`、`context_refs`，可选 `model`、`run_metadata`。输出包括 `trace/`、原始 `results/`、`summary.csv` 和 `run.json`；未生成、规划失败、执行失败与结果差异分开计数。按公开精度比较规范副本，保留原始值。没有生成记录不能算正确；小样本结果只描述其输入、模型和执行轮次。

## 报告与历史

`runs/` 是 Git 忽略的本地运行证据，跨机器使用时单独归档。全量基线输出与独立运行报告分开维护。[旧矩阵](../matrix.md) 与 matrix_v2 中的日期化记录用于追溯；历史命令和历史 PASS 不替代本页当前流程或当次执行结果。
