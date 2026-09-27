---
name: semantic-analytics
description: 在语义层(WrenAI MDL)上构建并验证业务分析交付的领域无关方法——仿真造数、DuckDB 装载、双路径 SQL 回归验证、Agent 问数纪律、可选浏览器仪表盘。搭配领域包(如 wren-hr)使用;换业务域时复用本技能的全部机制。
---

把一个业务域做成"可被 Coding Agent 正确问数"的交付。跳过铺垫,按阶段执行;每个阶段有明确的完成标志,不要跳过验证阶段。

## 前置依赖

- wren CLI(`pip install 'wrenai[memory]==0.13.4'`,内含 DuckDB 1.5)
- Python `duckdb` 模块(A 路径直连物理表用):`pip install duckdb`
- 仿真数据一律固定随机种子,交付物必须完全可复现

## 工作流(7 阶段)

```text
① scaffold → ② seed → ③ load → ④ model → ⑤ rules → ⑥ validate → ⑦ dashboard(可选)
      └──────────── 每阶段产物是下一阶段的输入,验证不过不进入下一阶段 ────────────┘
```

### ① 脚手架

```bash
python3 <本技能>/scripts/scaffold.py --domain finance --root <项目根>
```

生成 `db/seed`、`db/duckdb`、`semantic/{models,relationships.yml,knowledge}`、`validation` 骨架与占位文件。

### ② 造数

- 写 `gen_<域>_data.py`:固定随机种子;数据快照截止日**显式定义为常量**,不要依赖 `current_date`
- 业务规律要真实(薪酬挂钩职级/城市、季节性、漏斗闭环),否则问数演示没有说服力
- 输出 CSV 到 `db/seed/out*/`,一行一实体,文件名 = 表名
- 完成标志:重跑生成器,`git status` 零差异

### ③ 装载

```bash
python3 <本技能>/scripts/load_db.py --csv-dir <域>/db/seed/out --db <域>/db/duckdb/<域>.duckdb
```

- DuckDB 文件放进一个目录,连接 profile 的 `url` 指向**该目录**,文件名即 catalog 别名(MDL 里以 `"schema"` 前缀限定物理表时依赖此约定)
- CSV 一律 UTF-8;自动类型识别够用时用 `read_csv_auto`,金额等精确数值列建议显式 schema

### ④ 语义建模

- 每表一个 `models/<表名>/metadata.yml`(`name/table_reference/primary_key/columns`,列要有中文 description——它们是 NL2SQL 的功能输入,不是注释)
- 跨表关系写 `relationships.yml`;复用投影放 views,聚合放 cubes;**算术放查询层或 cube 度量,不放视图投影**(规避已知的规划退化)
- 构建:`wren context validate && wren context build`

### ⑤ 业务口径

- `knowledge/rules/general.md`:指标公式、时间范围、易混定义(如"离职率=期间离职÷期初在职"),**这是问数 Agent 的第一上下文**
- `knowledge/sql/`:已验证的自然语言/SQL 对照示例
- 完成标志:每条口径都能在规则文件里找到唯一出处

### ⑥ 双路径验证(质量门禁,不可跳过)

```bash
WREN_BIN=<wren路径> python3 <本技能>/scripts/run_all.py \
  --questions <域>/validation/questions.py --project <语义项目目录> \
  --db <域>/db/duckdb/<域>.duckdb --results <域>/validation/results
```

- 题库 `questions.py`:每题含 `id/domain/priority/question/gt/wren` 五要素;`gt` 直连物理表(标准答案),`wren` 经语义层(被测)
- 逐值比对:排序后比较,数值容差默认 0.011(`--tol` 可调),空结果判失败
- **纪律**:不为通过测试修改正确口径;报告通过率时区分历史记录与本次实际执行

### ⑦ 仪表盘(可选)

- 单文件 HTML:加载 mdl.json + 各表 parquet 快照,wren-core-wasm 浏览器内执行语义 SQL,ECharts 渲染
- 数据更新后必须重新导出快照并核对页面数字

## Agent 问数纪律(回答业务问题时)

```text
context instructions → memory fetch/recall → dry-plan → query → 验证后 memory store
```

- 结果必须来自实际查询;失败要如实说,不能当成零或无数据
- 时间敏感问题先核查 SQL 里有没有 `current_date`,与快照日不一致要显式说明
- 缺数据或缺口径时指出具体缺口,不猜

## 可替换点

- 语义引擎当前耦合 wren CLI(profile/memory/dry-plan)。换引擎时替换 ④⑥⑦ 三步的工具调用,管道形状(造数→库→语义→双路径验证)不变
- `scripts/run_all.py` 的比对核心(排序+容差+空结果失败)是引擎无关的,gt 侧换成其他执行器只需改 `run_gt`
