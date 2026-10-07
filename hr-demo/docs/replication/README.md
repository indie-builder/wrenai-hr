# 将分析流程迁移到新业务

<!-- docs:examples -->

本页以已有 PostgreSQL 数据库和新项目 `sales-bi` 为例。路径、连接和问题均须替换。复现现有 HR 演示请使用[环境与首次初始化](../../README.md#环境与首次初始化)，不要执行本页的新项目命令。

## 准备业务输入

先整理 5 至 10 个首批问题。每题记录日期范围、维度、单位、优先级、数据来源和负责确认口径的人，保存到新项目 `GOAL.md`。未明确的定义保留为待确认项。

让 Agent 只读盘点相关表，将以下内容写入 `docs/data-dictionary.md`：

- 每行的业务含义、主外键、关联基数及 JOIN 是否放大金额。
- 时间字段、时区、金额单位、状态枚举、软删除规则。
- 覆盖时间、刷新频率、空值、重复键、缺失关联，以及能否还原历史归属。

口径与标准答案由业务负责人核对。两条 SQL 结果相等不能代替这一步。数据不支持的问题必须记录缺口。

## 创建独立环境和连接

以下命令在同一终端执行。示例以本项目验证过的 Wren 0.15.0 和 Python 3.14 建立基线。不要复制旧虚拟环境。

```bash
export PROJECT_ROOT="$HOME/Documents/myself/sales-bi"
mkdir -p "$PROJECT_ROOT"
cd "$PROJECT_ROOT"
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install 'wrenai[postgres,memory]==0.15.0'
wren --version
wren context init --path "$PROJECT_ROOT/wren-project" --empty
mkdir -p docs queries validation/gt validation/results
cd "$PROJECT_ROOT/wren-project"
mkdir -p knowledge/rules knowledge/sql
wren profile add sales_demo --interactive
wren context set-profile sales_demo
wren profile debug
```

交互配置选择 PostgreSQL，使用获得授权的只读账号，并按数据源要求填写 SSL。profile 存在用户配置中，换机器后需要重建。保存成功不能代替连接验证成功。

让 Agent 将 `wren_project.yml` 项目名设为 `sales_demo`，保留该 CLI 版本要求的字段，确认连接没有指向 HR 数据库。升级 CLI 时先核对相关 `--help`。

## 建模与问数

领域无关的[语义分析技能](../../../.agents/skills/semantic-analytics/SKILL.md)维护建模、口径和验证方法。将以下新业务内容交给 Agent 实现：

1. 在 `knowledge/rules/general.md` 写明指标公式、分子分母、去重键、日期边界、状态过滤、单位、空值和零分母处理。统一实际日期或固定快照日期。
2. 在 `validation/gt/` 保存直接查询物理表的独立标准 SQL。用可信报表或业务核对确认结果，例如净销售额是否扣退款、按支付日还是下单日统计。
3. 在 `models/` 定义物理映射、主键、类型、描述和枚举，在 `relationships.yml` 核对关联基数。按问题需要补充 `views/` 和 `cubes/`。
4. 运行以下构建；失败先修正，再使用产物。

```bash
wren context validate
wren context build
wren context show
wren memory index
```

将[HR Agent 工作流](../../wren-project/AGENTS.md#回答业务数据问题)改为新项目的路径和连接。每个新会话读取业务规则，fetch 模型、recall 已确认查询，使用 MDL 名称编写 SQL，先 dry-plan 再 query。答案包含实际结果、日期、口径和限制，失败不能解释为零。

首题 SQL 保存到 `$PROJECT_ROOT/queries/q01.sql`，只包含 SQL。查询示例：

```bash
wren context instructions
wren memory fetch -q "2025 年月度净销售额是多少？"
wren memory recall -q "2025 年月度净销售额是多少？" --limit 3
wren dry-plan --sql "$(cat "$PROJECT_ROOT/queries/q01.sql")"
wren query --sql "$(cat "$PROJECT_ROOT/queries/q01.sql")" -o csv -q \
  > "$PROJECT_ROOT/validation/results/q01.wren.csv"
```

结果确认后再存储，检查生成文件、SQL 完整性和召回结果，避免覆盖其他问题：

```bash
wren memory store --nl "2025 年月度净销售额是多少？" \
  --sql "$(cat "$PROJECT_ROOT/queries/q01.sql")"
wren memory recall -q "2025 年月度净销售额是多少？" --limit 3
```

## 建立两种独立验证

让 Agent 参考[题库](../../validation/v2/questions.py)、[共享执行核心](../../../src/hr_analytics/execution.py)和[比较规则](../../../src/hr_contracts/tables.py)，创建新项目的 `validation/run_all.py`。标准路径直连 PostgreSQL，被测路径经过 Wren，使用相同数据时点。HR runner 的 DuckDB 连接不能直接复用。

验证器必须检查退出码和超时，区分失败与合法空结果，比较列名、行数、数值精度和必要顺序。保留题号、业务域、两条 SQL、汇总与执行证据；子集运行不得覆盖全量报告，失败返回非零退出码。

```bash
cd "$PROJECT_ROOT"
python validation/run_all.py --only q01
python validation/run_all.py
```

自然语言生成另行验收，流程参考[评测协议](../../validation/v2/README.md#自然语言评测)。生成者只能看到问题，生成后才与独立答案比较。分别测试原题、同义改写、新日期或维度、含糊及数据不支持的问题；保留未存入查询记忆的新题，分别报告已知题与新题结果。固定 SQL 通过率不是自然语言准确率。

## 交付仪表盘与复现说明

先确定指标、使用人群和允许送入浏览器的字段。快照模式下，获得文件的人能读取其中数据。

```bash
cd "$PROJECT_ROOT/wren-project"
wren genbi build sales-overview --data-mode snapshot \
  --prompt "制作已验证销售指标的仪表盘，显示口径、截至日期和加载错误" \
  > "$PROJECT_ROOT/docs/dashboard-build-instructions.txt"
```

`genbi build` 输出构建说明。让 Agent 阅读说明并创建应用、可重跑的快照导出脚本、加载及错误状态，再按 `wren genbi register --help` 注册应用。

```bash
wren genbi verify sales-overview
wren genbi open sales-overview --port 8318
```

打开 <http://127.0.0.1:8318>，核对图表与已验证数字。文件预检不能代替浏览器验收。已有服务占用端口时不要重复启动。

在新项目 README 写明环境安装、profile 配置、构建、查询、回归、快照刷新和预览命令，记录业务范围、失败和已知限制。保存依赖版本，凭据、环境、私有数据及缓存不进 Git。用干净环境实际演练一次复现。公网部署单独确定访问控制和数据范围。

新增问题先检查数据与模型，补独立标准答案后验证。模型、关系或共享口径变化后执行 validate、build、index 和全量回归，刷新受影响仪表盘。

## 不要照搬的 HR 约定

- `db/load_duckdb.sh` 会替换专用演示数据库，不能用于需要保留人工修改的库。
- HR 快照日、人员归属规则和查询记忆不适用于新业务。
- 物理金额与语义金额的类型可能不同；本项目的 DOUBLE 绕行不保证新业务的财务精度。
- 不复制旧环境、凭据、索引或 HR 数据。模型 YAML 是本项目权威来源，生成的 MDL 不是编辑入口。

参考[员工模型](../../wren-project/models/employees/metadata.yml)、[离职 Cube](../../wren-project/cubes/attrition/metadata.yml)、[业务规则](../../wren-project/knowledge/rules/general.md)和[快照导出](../../scripts/export_dashboard.py)。历史目标与验收见 [GOAL.md](../../GOAL.md) 和[验证记录](../../validation/v2/matrix_v2.md)，当前状态以本次执行为准。
