# WrenAI HR 分析演示工作区

基于 WrenAI 当前 Agent 驱动的 GenBI 架构，演示 HR 数据建模、业务问数、固定 SQL 回归和浏览器仪表盘。公司“星辰科技”与全部人员数据均为虚构，数据快照日固定为 **2026-08-31**。

`hr-demo/` 集中维护演示的数据源、语义定义、仪表盘、验证程序和文档，包含规范源与可重建交付产物。根目录 `hr_mcp/`、`hr_query/` 维护查询运行代码；MCP 构建使用本工作区，线上函数不打包整个目录。

- 完整的按任务路由见根 [AGENTS.md](../AGENTS.md#按任务读取)。
- 本页章节：[环境与首次初始化](#环境与首次初始化)、[构建、问数与更新](#构建问数与更新)、[验证](#验证)、[文档检查](#文档检查)、[仪表盘快照与本地预览](#仪表盘快照与本地预览)。
- 语义工作流见 [语义项目 AGENTS](wren-project/AGENTS.md)；固定 SQL 回归与自然语言评测见 [验证入口](validation/v2/README.md)。

## 交付范围

| 层次 | 实现 | 范围 |
| --- | --- | --- |
| 数据 | `db/`，DuckDB 单文件 | 25 表、273,515 行仿真数据；无需 Docker |
| 语义 | `wren-project/` | 25 模型、32 关系、6 视图、6 Cube |
| 知识 | `knowledge/rules/`、`knowledge/sql/` | 业务口径、枚举与已确认查询示例 |
| 仪表盘 | `apps/hr-overview/` | 9 KPI、12 个图表区域；语义 SQL 与 Cube 共用查询配置 |
| 验证 | `validation/v2/` | 41 道固定 SQL 双路径回归；自然语言评测单独记录 |

八类业务域包括组织编制、员工生命周期、薪酬福利、考勤假期、招聘用工、绩效发展、员工关系、人效分析。项目没有营收数据，“人效”是人员与成本分析，不能解释为完整经营产出效率。

本项目不包含人事事务录入、审批系统、真实 HR 接入或生产行列权限。提供独立的 [Vercel MCP 服务](docs/mcp-vercel.md)，使用 Streamable HTTP 与 Bearer Token 向自有客户端开放只读分析；持有 Token 可查询全部仿真数据。智能问数由外部 Agent 承担，Wren CLI 本身不调用大模型生成答案；`wren ask` 只输出给 Agent 的提示词。Agent 是否联网、需要何种凭据，由使用的 Agent 环境决定。

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
(cd hr-demo/db && ./load_duckdb.sh)
```

该命令在临时库装载成功后**替换** `db/duckdb/public.duckdb`，不要对含有需保留人工修改的库执行。默认考勤种子缺失或校验不符会失败，不自动随机回退；考勤数据只来自 SHA-256 校验通过的快照。库名固定为 `public.duckdb`，因为 Wren 按文件名挂载物理 catalog。

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
        'url': str(root / 'hr-demo/db/duckdb'),
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
  cd hr-demo/wren-project
  ../../.venv/bin/wren context set-profile hr_demo_duck
  ../../.venv/bin/wren profile debug
)
```

已有正确 profile 时跳过创建。移动或重命名工作区后，重新执行上述 profile 配置，更新 `hr_demo_duck` 的数据库目录，并用 `wren profile debug` 确认连接；现有数据库随目录移动，无需重新建库。CI 使用独立 `WREN_HOME`，不覆盖用户配置。`.env` 仅在连接需要环境变量时提供，不是本地 DuckDB 回归的必需文件。

## 构建、问数与更新

模型 YAML 是唯一业务定义来源；修改模型或规则后的 validate/build/index 命令以 [语义项目工作流](wren-project/AGENTS.md#修改模型规则与知识) 为准。问数按其中的 [回答业务数据问题](wren-project/AGENTS.md#回答业务数据问题) 流程获取规则、schema 和查询示例，优先复用已有 Cube，验证 SQL 后执行并存储确认结果；该入口也说明 fetch 完整输出过大时的分段读取，以及搜索过滤参数的适用范围。

执行 `wren memory check` 检查文件与索引漂移；若改名后旧查询仍被召回，先备份 `.wren/memory/`，再执行 `wren memory reset --force`、`wren memory index`、`wren memory check`。reset 只清理衍生索引，保留 `knowledge/sql/*.md`；不要删除知识源文件来掩盖索引问题。

例如检查并查询 2026H1 各月总成本和发薪人次：

```bash
(
  cd hr-demo/wren-project
  ../../.venv/bin/wren cube query --cube total_cost \
    --measures total_cost,person_months --dimensions pay_period \
    --filter 'pay_period:gte:2026-01' --filter 'pay_period:lte:2026-06'
)
```

Cube 返回结果不承诺顺序；展示趋势时显式按月份排序。模型校验统一使用 `wren context validate` 和 `check_semantics.py --build-check`；完整 `target/mdl.json` 是本地构建缓存，不纳入 Git。MCP 构建直接读取同一份 YAML，CI 将轻量编译结果与 Wren 官方构建逐项核对。

## 验证

[当前验证入口](validation/v2/README.md) 维护固定 SQL 全量/子集回归、比较规则、工具测试、语义构建检查与独立自然语言评测的命令；回归、自然语言评测与仪表盘导出共用 [result_contract.py](validation/v2/result_contract.py) 的比较与输出约定。根据改动选择检查，使用当次退出码和报告判断结果；历史记录仅用于追溯。

CI 配置见 [hr-demo.yml](../.github/workflows/hr-demo.yml)：文档检查独立运行；分析检查在新工作区安装依赖、建库、配置隔离 profile、运行回归和快照检查。云端 CI 是否通过以 GitHub 实际运行记录为准。

## 文档检查

从仓库根运行以下只读检查，仅使用 Python 标准库与 Git，无需创建数据库或虚拟环境：

```bash
python3 -m unittest discover -s scripts/tests -p 'test_check_docs.py' -v
python3 scripts/check_docs.py
```

检查范围为仓库自身的 README、AGENTS、`hr-demo/docs/` 和验证说明；查询知识源及外部技能不在该检查范围内。检查本地 Markdown 链接目标，以及当前 bash/sh 命令中的脚本、依赖文件、工作目录和静态目录路径。源码路径以 Git 跟踪文件及未忽略的待添加文件为依据，忽略的本地数据库或环境不能让路径检查通过。另对全部跟踪的文本文件扫描已废弃的旧交付目录路径段（取值定义在检查器内）：带历史或模板标记的文档豁免，检查器自身的测试夹具除外。外部链接、CLI 参数兼容性、页面渲染与命令执行结果需各自验证；该检查不执行文档命令，也不验证标题锚点。

保留历史或新业务模板时，在文件顶部放对应标记，并紧随说明其身份与当前入口：`<!-- docs:historical -->` 标记整份历史记录，`<!-- docs:examples -->` 标记新业务模板。混合文件可用 `<!-- docs:historical:start -->` 与 `<!-- docs:historical:end -->` 包住历史段。标记跳过命令路径检查，本地 Markdown 链接仍检查。当前操作入口保持独立，不能用这些标记掩盖失效命令。

## 仪表盘快照与本地预览

模型构建和数据库准备好后：

```bash
.venv/bin/python hr-demo/scripts/export_dashboard.py
# 不写产物，检查 MDL、数据和页面快照是否一致
.venv/bin/python hr-demo/scripts/export_dashboard.py --check
(
  cd hr-demo/wren-project
  ../../.venv/bin/wren genbi verify hr-overview
)
python3 -m http.server 8317 --bind 127.0.0.1 \
  --directory hr-demo/wren-project/apps/hr-overview
```

打开 <http://127.0.0.1:8317>。如果已有服务占用该端口，检查并复用，不重复启动。文件预检不能替代浏览器图表、错误状态及数字核对。`wren genbi build` 只返回构建指令，完整应用由 Agent 按指令实现。

导出只读取 DuckDB，按 `query-spec.json` 明确的表列需求生成快照和来源清单。页面 `mdl.json`、`snapshot-manifest.json` 与完整 `target/mdl.json` 均为 Git 忽略的生成产物；新检出先构建模型并导出，再预览。CI 同样从源构建、检查，上传完整 `hr-dashboard` 静态应用供下载预览。23 项查询中 11 项调用 Cube，导出 12 表、53 列；裁剪前后的完整体积与比例审计见 [验证记录](validation/v2/matrix_v2.md)。清单记录来源、列清单和内容哈希，`--check` 检查源数据与页面查询结果；数据、模型或导出程序变化后重新导出。

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
hr-demo/
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
