# WrenAI HR 分析与验证交付

基于 WrenAI 当前 Agent 驱动的 GenBI 架构，演示 HR 数据建模、业务问数、固定 SQL 回归和浏览器仪表盘。公司“星辰科技”与全部人员数据均为虚构，数据快照日固定为 **2026-08-31**。

[迁移到新业务的操作指南](docs/replication/README.md) · [业务规则](wren-project/knowledge/rules/general.md) · [验证记录](validation/v2/matrix_v2.md) · [历史目标](GOAL.md)

## 交付范围

| 层次 | 实现 | 范围 |
| --- | --- | --- |
| 数据 | `db/`，DuckDB 单文件 | 25 表、273,515 行仿真数据；无需 Docker |
| 语义 | `wren-project/` | 25 模型、32 关系、6 视图、6 Cube |
| 知识 | `knowledge/rules/`、`knowledge/sql/` | 业务口径、枚举与已确认查询示例 |
| 仪表盘 | `apps/hr-overview/` | 9 KPI、12 个图表区域；语义 SQL 与 Cube 共用查询配置 |
| 验证 | `validation/v2/` | 41 道固定 SQL 双路径回归；自然语言评测单独记录 |

八类业务域包括组织编制、员工生命周期、薪酬福利、考勤假期、招聘用工、绩效发展、员工关系、人效分析。项目没有营收数据，“人效”是人员与成本分析，不能解释为完整经营产出效率。

本项目不包含人事事务录入、审批系统、真实 HR 接入、生产行列权限或公网部署。智能问数由外部 Agent 承担，Wren CLI 本身不调用大模型生成答案；`wren ask` 只输出给 Agent 的提示词。Agent 是否联网、需要何种凭据，由使用的 Agent 环境决定。

## 数据与查询路径

- **数据来源：**基线/扩展 CSV 和 `db/seed/attendance_records.parquet` → DuckDB `public.duckdb`。考勤是保留的确定性种子，附 SHA-256 来源清单，不依赖仪表盘目录。
- **临时问数：**Agent → 业务规则、上下文检索和历史查询 → Cube 或 MDL SQL → Wren 规划 → DuckDB → 结果解释。
- **仪表盘：**只读导出脚本 → 必要的 Parquet、裁剪后的 MDL、快照清单 → 浏览器 Wren WASM → ECharts。
- **固定 SQL 回归：**A 路径直接查询物理表；B 路径经 Wren 查询同一 DuckDB。两路相等证明相应查询的结果一致，不能独立证明业务定义正确。

数据库更新不会自动刷新页面；模型和快照通过导出命令同步。浏览器需要访问 ECharts 与 WASM CDN，首次打开需要网络。客户端计算不构成数据权限边界，获得页面快照文件的人能够读取其中数据。

## 环境与首次初始化

以下命令从**仓库根目录**执行，推荐 Python 3.12。不要复制其他目录的虚拟环境。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-demo.txt
# 需要 embedding 检索时安装；固定 SQL 回归和导出无需此项
.venv/bin/python -m pip install 'wrenai[memory]==0.13.4'
.venv/bin/wren --version
```

[requirements-demo.txt](../requirements-demo.txt)固定 Wren 0.13.4、引擎、DuckDB、SQLGlot、Arrow、YAML 的已验证版本；它不是包含全部传递依赖的锁文件。

首次创建或明确需要重建演示库时执行：

```bash
(cd hr-delivery/db && ./load_duckdb.sh)
```

该命令在临时库装载成功后**替换** `db/duckdb/public.duckdb`，不要对含有需保留人工修改的库执行。默认考勤种子缺失或校验不符会失败，不自动随机回退。`--attendance generate` 明确生成另一批考勤，执行后必须重新回归、导出快照，不能沿用既有验证数字。库名固定为 `public.duckdb`，因为 Wren 按文件名挂载物理 catalog。

用户级连接 profile 不随仓库分发。首次在新机器创建 `hr_demo_duck`：

```bash
.venv/bin/python - <<'PY'
import json
import subprocess
import tempfile
from pathlib import Path
root = Path.cwd()
config = {
    'datasource': 'duckdb',
    'properties': {
        'url': str(root / 'hr-delivery/db/duckdb'),
        'format': 'duckdb',
    },
}
with tempfile.TemporaryDirectory() as folder:
    path = Path(folder) / 'profile.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    subprocess.run([str(root / '.venv/bin/wren'), 'profile', 'add',
                    'hr_demo_duck', '--from-file', str(path)], check=True)
PY
(
  cd hr-delivery/wren-project
  ../../.venv/bin/wren context set-profile hr_demo_duck
  ../../.venv/bin/wren profile debug
)
```

已有正确 profile 时跳过创建。CI 使用独立 `WREN_HOME`，不覆盖用户配置。`.env` 仅在连接需要环境变量时提供，不是本地 DuckDB 回归的必需文件。

## 构建、问数与更新

模型 YAML 是唯一业务定义来源；修改模型或规则后：

```bash
(
  cd hr-delivery/wren-project
  ../../.venv/bin/wren context validate
  ../../.venv/bin/wren context build
  ../../.venv/bin/wren memory index
)
```

Agent 首次使用读取 `wren skills get usage` 与 `wren context instructions`，随后按问题 `memory fetch`、`memory recall`。聚合问题先检查 `cube list` / `cube describe`，匹配已有指标时优先 Cube，其余问题使用 MDL SQL，先 dry-plan 再 query。确认答案后存入查询记忆，并检查写入文件及召回结果。执行 `wren memory check` 检查文件与索引漂移；若改名后旧查询仍被召回，先备份 `.wren/memory/`，再执行 `wren memory reset --force`、`wren memory index`、`wren memory check`。reset 只清理衍生索引，保留 `knowledge/sql/*.md`；不要删除知识源文件来掩盖索引问题。

例如检查并查询 2026H1 各月总成本和发薪人次：

```bash
(
  cd hr-delivery/wren-project
  ../../.venv/bin/wren cube query --cube total_cost \
    --measures total_cost,person_months --dimensions pay_period \
    --filter 'pay_period:gte:2026-01' --filter 'pay_period:lte:2026-06'
)
```

Cube 返回结果不承诺顺序；展示趋势时显式按月份排序。`gen_models_v2.py` 现在只校验规范模型源文件，显式 `--output-dir` 可复制规范定义到另一目录，不再从旧硬编码模板覆盖模型。

## 验证

```bash
# 固定 SQL 回归：41 题
python3 hr-delivery/validation/v2/run_all.py
# 只跑子集，报告与完整汇总分开
python3 hr-delivery/validation/v2/run_all.py --only q03 q18
python3 hr-delivery/validation/v2/run_all.py --domain 人效分析
# 验证程序单元测试与源文件/构建产物一致性
.venv/bin/python -m unittest discover -s hr-delivery/validation/v2/tests
.venv/bin/python hr-delivery/validation/v2/check_semantics.py --build-check
```

完整回归结果写入 `validation/v2/summary.csv` 和 `results/`。以当次退出码、汇总与明细判断是否通过。排名及趋势题声明顺序要求；数值比较默认绝对容差 0.011，按题可另设精度与合法空结果策略。这个精度不是所有财务场景的通用要求。

自然语言评测与固定 SQL 回归分开：评测入口导出问题、公开输出列/展示精度和必要业务上下文，生成端不读取标准答案 SQL 或结果；实际 Agent 提交生成 SQL 和上下文记录后，再执行 Wren 规划、受限只读 DuckDB 查询并与标准答案比较。没有生成记录的题目不会被算作正确。本项目的 SQL 回归结果**不能称为自然语言问数准确率**。

```bash
# 输出目录须为新目录；可选 --only q03 q18 筛选题目
.venv/bin/python hr-delivery/validation/v2/eval/nl_eval.py export \
  --output-dir /tmp/hr-nl-package
# 让独立 Agent 只读题目包，按 protocol.json 写真实生成记录
.venv/bin/python hr-delivery/validation/v2/eval/nl_eval.py run \
  --records /tmp/hr-generated.jsonl \
  --output-dir hr-delivery/validation/v2/runs/nl-first
```

JSONL 必填 `id`、原始 `question`、`generated_sql`、`context_refs`；可选 `model`、`run_metadata`。输出保留 `trace/`、原始 `results/`、`summary.csv`、`run.json`；未生成、规划失败、执行失败和结果差异分别计数。比较列位置并按公开精度规范副本，不篡改原始结果。评测器不调用付费模型；使用者负责记录真实生成来源。

CI 配置见 [hr-demo.yml](../.github/workflows/hr-demo.yml)：在新工作区安装依赖、建库、配置隔离 profile、运行回归和快照检查。云端 CI 是否通过以 GitHub 实际运行记录为准。

## 仪表盘快照与本地预览

模型构建和数据库准备好后：

```bash
.venv/bin/python hr-delivery/scripts/export_dashboard.py
# 不写产物，检查 MDL、数据和页面快照是否一致
.venv/bin/python hr-delivery/scripts/export_dashboard.py --check
(
  cd hr-delivery/wren-project
  ../../.venv/bin/wren genbi verify hr-overview
)
python3 -m http.server 8317 --bind 127.0.0.1 \
  --directory hr-delivery/wren-project/apps/hr-overview
```

打开 <http://127.0.0.1:8317>。如果已有服务占用该端口，检查并复用，不重复启动。文件预检不能替代浏览器图表、错误状态及数字核对。

导出只读取 DuckDB，按页面明确的表列需求生成快照，同时生成可检查的来源清单；不是整库复制。当前共享配置为 `query-spec.json`，23 项查询中 11 项调用 Cube；导出 12 表、53 列，Parquet 共 281,959 字节，比原 25 表快照减少 86.8%（不计 WASM/CDN）。`snapshot-manifest.json` 记录来源、列清单和内容哈希，`--check` 同时检查源数据与页面查询结果，数据或模型变化后需重新导出。

页面保留必要分析字段，避免把姓名、生日、邮箱和手机号等不用于图表的字段默认下发。即便经过裁剪，仿真快照仍不等于可直接用于真实 HR 数据的权限方案。

## 业务口径与限制

- “当前”固定指 2026-08-31。年龄按年份相减，**不是周岁**；司龄按日期差 / 365.25。合同预警为履行中、到期日在快照日至后 90 天之间，包含两端。
- 离职率 = 期间离职人数 / 期初在职人数，包括主动、被动、实习、外包；不能用当前在职员工代替历史期初人群。
- 历史部门指标按员工当前档案部门归属，未还原调岗历史。
- 应发成本与含社保公积金企业缴纳的总成本区分；人均月总成本采用期间发薪人次作为分母。
- 晋升率按期间去重员工数；晋升幅度在查询中用前后工资计算，晋升原因字段保留文本。
- Offer 接受率 = 已接受 /（已接受 + 已拒绝），不含待回复。
- 金额 DOUBLE 是本项目针对既有引擎规划限制的处理，不推广为精确财务核算方案。
- 查询记忆保存的是确认过的示例，仍需核对新问题的日期、条件和口径。更新题库时同步相关记忆并重新索引。
- 既有 PostgreSQL 验收和迁移记录保存在矩阵中；当前运行只使用 DuckDB，不声称完成已取消的 PG↔DuckDB 交叉验证。

## 目录

```text
hr-delivery/
  db/                  schema、CSV/考勤种子、DuckDB 构建器
  wren-project/
    models/ views/ cubes/ relationships.yml
    knowledge/         业务规则与确认的查询示例
    target/mdl.json     从 YAML 构建的完整语义模型
    apps/hr-overview/   页面、共享查询、裁剪 MDL 与快照
  scripts/             仪表盘导出与一致性检查
  validation/v2/       固定题库、回归、自然语言评测与测试
  docs/replication/    新业务复刻指南
```

不提交 `.env`、用户级凭据、虚拟环境、本地 DuckDB、检索索引和临时评测运行目录。`GOAL.md` 和旧版验证资料属于历史记录，当前复现命令以本文为准。
