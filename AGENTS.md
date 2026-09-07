# AGENTS.md

## 项目定位与范围

本仓库是 WrenAI HR 数据分析演示与验证交付，主要工作区为 `hr-delivery/`。使用 PostgreSQL 仿真数据、Wren MDL 语义层、Agent 问数和浏览器仪表盘，不是完整的人事业务管理系统。

- 演示公司为虚构的“星辰科技”，数据快照截至 **2026-08-31**。
- 交付基线：25 张表、25 个模型、32 条关系、6 个视图、6 个 Cube。
- 仪表盘基线：9 个 KPI、12 张图；使用 MDL 与 Parquet 快照在浏览器内计算。
- 已有 41 道固定 SQL 双路径回归题。历史通过记录不能替代当前运行结果，也不能等同于自然语言生成准确率。
- 默认使用中文解释业务结果与项目变更。
- 本文件适用于整个仓库；进入 `hr-delivery/wren-project/` 工作时，同时遵守其 `AGENTS.md`。

## 先读哪些文件

| 路径 | 用途 |
| --- | --- |
| `README.md` | 总入口 |
| `hr-delivery/README.md` | 架构、复现步骤、已知限制 |
| `hr-delivery/GOAL.md` | 历史目标与验收记录；部分规划名称以实际文件为准 |
| `hr-delivery/docs/replication/README.md` | 迁移到新项目的完整操作指南 |
| `hr-delivery/wren-project/AGENTS.md` | 语义项目的 Agent 工作流 |
| `hr-delivery/wren-project/knowledge/rules/general.md` | 业务口径 |
| `hr-delivery/validation/v2/questions.py` | 题库、标准 SQL 与语义 SQL |
| `hr-delivery/validation/v2/matrix_v2.md` | 验证与问题处理记录 |

## 目录职责

- `hr-delivery/db/`：数据库 schema、模拟数据生成器、CSV 和装载脚本。
- `hr-delivery/wren-project/models/`：物理表映射、字段类型和描述。
- `hr-delivery/wren-project/relationships.yml`：模型关系。
- `hr-delivery/wren-project/views/`、`cubes/`：复用视图、聚合指标和分析维度。
- `hr-delivery/wren-project/knowledge/`：业务规则、词汇与已确认的自然语言/SQL 示例。
- `hr-delivery/wren-project/target/mdl.json`：语义模型构建产物，优先修改 YAML 源文件后构建。
- `hr-delivery/wren-project/apps/hr-overview/`：仪表盘 HTML、MDL 和 25 张 Parquet 快照。
- `hr-delivery/validation/v2/`：当前统一验证入口；上一层保留早期脚本和输出。
- `vendor/WrenAI/`：本地未修改的上游参考克隆，Git 忽略，其他机器可能不存在。
- `.venv/`、`.env`、`.wren/memory/`：本地环境、凭据、可重建索引，不提交。

## 环境与命令约定

以下根目录命令均从仓库根目录执行，不硬编码某台机器的绝对路径。

本项目已使用 Wren CLI `0.13.4`、PostgreSQL 16。复现环境优先参考交付 README；依赖缺失时在根目录建立虚拟环境：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install 'wrenai[postgres,memory]==0.13.4'
.venv/bin/wren --version
```

如果当前 Python 与依赖不兼容，使用 Python 3.12 建立独立环境。不要复制其他目录的 `.venv`。

数据库容器名为 `wrenai-hr-pg`，宿主端口 `25432`，数据库与 profile 名为 `hr_demo`，数据库用户为 `hr`。先检查服务，不重复创建或重建已有容器：

```bash
docker ps --filter name=wrenai-hr-pg
```

语义查询命令需要在语义项目目录运行；在单独子 Shell 中载入本地连接变量，避免改变后续根目录命令的工作目录：

```bash
(
  cd hr-delivery/wren-project
  set -a
  source .env
  set +a
  ../../.venv/bin/wren profile debug
)
```

新克隆不含 `.env` 和用户级 profile。缺少配置时，通过 `wren profile add hr_demo --interactive` 配置连接，再在语义目录执行 `wren context set-profile hr_demo`。根据连接实际需要设置本地环境变量，不在输出或文档中暴露凭据。当前 v2 runner 会无条件读取语义目录 `.env`；即使 profile 不使用环境变量，也需要存在该文件（可以为空）。

## 回答业务数据问题

先加载上下文，再生成并执行查询；不要用文档中的历史数字冒充实际查询结果。

在上述语义目录及环境中依次执行：

```bash
../../.venv/bin/wren context instructions
../../.venv/bin/wren memory fetch -q "用户问题"
../../.venv/bin/wren memory recall -q "用户问题" --limit 3
../../.venv/bin/wren dry-plan --sql "根据上下文编写的 SQL"
../../.venv/bin/wren query --sql "根据上下文编写的 SQL" -o csv -q
```

`context instructions` 每个新查询会话首次执行，规则修改后重新读取。SQL 使用 MDL 模型名；直接查询物理表仅用于标准答案核对或明确的数据诊断。

结果确认后保存并检查实际写入文件及召回结果：

```bash
../../.venv/bin/wren memory store --nl "用户问题" --sql "已验证的 SQL"
../../.venv/bin/wren memory recall -q "用户问题" --limit 3
```

回答需包含实际结果、计算口径、数据时间范围；用户要求时展示执行 SQL 和标准答案比对。执行失败应明确说明，不能当成零或无数据。缺少数据或定义时指出具体缺口。

`wren ask` 只包装给 Agent 的提示词，不会自行调用模型给出答案。

### 容易混淆的业务定义

以 `knowledge/rules/general.md` 为准，特别注意：

- 当前在职使用 `status='在职'`；历史期初人数根据入职、离职日期还原，不能只筛当前在职。
- 离职率 = 期间离职人数 / 期初在职人数，包含主动、被动、实习、外包。
- `employees.dept_id` 是当前档案部门。按它计算历史部门指标时说明未还原调岗历史。
- 人力成本的应发口径与包含社保公积金企业缴纳的人力总成本需区分。
- Offer 接受率 = 已接受 / (已接受 + 已拒绝)，排除待回复。
- 数据快照日为 2026-08-31，但部分计算列使用 `current_date`；时间敏感问题先核查 SQL，并说明或处理差异，不能声称两者天然一致。

## 修改模型与业务规则

1. 修改 YAML 源文件或 `knowledge/rules/`，不要仅修改构建产物。
2. 在语义目录执行以下命令；失败先修正，不继续依赖失败产物。

```bash
../../.venv/bin/wren context validate
../../.venv/bin/wren context build
../../.venv/bin/wren memory index
```

3. 对受影响问题执行回归；共享模型、关系和指标口径修改后运行全量回归。
4. 若仪表盘受影响，同步应用使用的 MDL、快照和 SQL，并校验数字。

已知限制来自本项目验证记录，应结合当前版本实际重现后处理：

- 部分 DECIMAL 规划退化问题在最终 MDL 中以 DOUBLE 绕行；不要把此处理直接推广到所有业务，特别是精确金额计算。
- `gen_models_v2.py` 仍保留部分旧 DECIMAL 和晋升幅度计算列定义。重跑前先与最终模型对齐，防止覆盖修正。
- 已有视图主要执行关联投影，算术放在查询或 Cube 层，以避开已记录的规划问题。
- `wren memory store` 曾出现中文文件名异常。检查文件内容、是否覆盖和能否召回，不能只看调用成功。

## 验证

从仓库根目录执行：

```bash
python3 hr-delivery/validation/v2/run_all.py --only q03
python3 hr-delivery/validation/v2/run_all.py --domain 人效分析
python3 hr-delivery/validation/v2/run_all.py
```

- A 路径：题库 `gt` SQL 通过 Docker 内 `psql` 查询物理表。
- B 路径：题库 `wren` SQL 经根目录 `.venv/bin/wren` 执行。
- 输出为 `summary.csv` 与 `results/qXX.{gt,wren}.csv`。脚本注释虽提到自动 Markdown 矩阵，当前实现没有生成它，不要声称生成。
- 每次运行覆盖 `summary.csv`，单题/单域运行后只剩该子集汇总；需要完整交付报告时重跑全量。
- 当前 runner 数值容差为 `0.011`，排序后比对，且将空结果判失败。它不验证排名原始顺序，也不代表适用于所有新业务的精度要求。
- runner 主要从错误文本判断失败；异常时同时检查输出和底层执行状态，避免把异常当作有效结果。
- 这是固定 SQL 回归，不包含每题实时调用模型生成 SQL。评估自然语言能力需单独保留问题输入、生成 SQL、结果和标准答案对照。
- 不为通过测试而修改正确业务口径。报告通过率时区分历史记录与本次实际执行。
- 纯文档修改检查路径、命令与描述一致即可，不必重复运行全量数据库回归。

## 仪表盘

在根目录执行本地预览：

```bash
python3 -m http.server 8317 --bind 127.0.0.1 \
  --directory hr-delivery/wren-project/apps/hr-overview
```

打开 `http://127.0.0.1:8317`；若已有服务占用该端口，先检查，不重复启动。

也可在语义目录执行 `../../.venv/bin/wren genbi verify hr-overview` 检查已注册应用文件。文件预检不能替代浏览器图表、加载错误和数字一致性检查。

页面使用 ECharts 和 `@wrenai/wren-core-wasm` 的 CDN 资源，首次加载需要网络。数据库更新不会自动刷新 Parquet；修改数据后需重新导出相应快照并核对结果。`wren genbi build` 输出构建指令，不会自动写出完整应用。

## 数据与版本管理

- `hr-delivery/db/load.sh` 会执行 `DROP SCHEMA public CASCADE`。它只用于明确要求重建的专用演示库，普通查询、文档修改或验证不运行它。
- 仿真 CSV、仪表盘 Parquet、验证结果是交付物；修改时关注来源、时间范围和可复现性。
- 不提交 `.env`、虚拟环境、用户级连接凭据和 `.wren/memory/` 缓存；保持 `.gitignore` 生效。
- 本项目已发布到公开仓库，提交前检查差异中是否混入实际凭据、真实个人数据或本地产物。
- 旧 `validation/run_wren.sh` 含机器绝对路径，优先使用 v2 runner。
- 不默认修改或提交 `vendor/WrenAI/` 上游参考源码。
- 完成工作后说明修改的文件、实际执行的验证和仍未验证的部分。
