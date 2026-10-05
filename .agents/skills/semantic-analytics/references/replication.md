# 复现方法(领域无关)

从零把一个业务域做到"可被 Coding Agent 正确问数"的完整步骤。本指南是
semantic-analytics 技能的展开;各域的领域包(数据、口径、题库)不在这里。

## 0. 环境

```bash
python3 -m venv .venv
.venv/bin/python -m pip install 'wrenai[memory]==0.13.4' duckdb
.venv/bin/wren --version   # 0.13.4, 核心自带 DuckDB 1.5
```

新机器没有用户级 profile 时,为语义项目绑定数据源:

```bash
wren profile add <profile名> --from-file <json>
# json 形如 {"datasource": "duckdb", "properties": {"url": "<库文件所在目录>", "format": "duckdb"}}
cd <语义项目目录> && wren context set-profile <profile名>
```

约定:DuckDB 库文件放在一个目录里,profile 的 `url` 指向该目录;
**文件名 = catalog 别名**,MDL 的 `schema` 字段应与文件名 stem 一致。
语义项目目录建议提供 `.env`(可为空;缺失时技能 runner 跳过不报错),
凭据不进代码。

## 1. 造数 → 2. 装载 → 3. 建模 → 4. 口径

见 SKILL.md 工作流 ②~⑤ 阶段;命令在阶段内。两条硬规则:

- 仿真数据固定随机种子,重跑生成器后 `git status` 必须零差异;
- 列/模型的 description 是 NL2SQL 的功能输入,质量决定问数准确率。

## 5. 双路径验证

```bash
WREN_BIN=$PWD/.venv/bin/wren .venv/bin/python <技能>/scripts/run_all.py \
  --questions <域>/validation/questions.py --project <域>/semantic \
  --db <域>/db/duckdb/<域>.duckdb --results <域>/validation/results
```

- 输出 `qXX.{gt,wren}.csv`(仅通过题)与 `summary.csv`(全量运行;子集写 `runs/<子集>/`);全过退出码 0
- 数值容差默认 0.011,拒绝 NaN/Inf;无序题多重集比对,题级 `ordered=True` 逐行保序;空结果默认判失败(`allow_empty=True` 放行)
- runner 以子进程退出码与超时判定执行失败;报告只记录错误类别,不写入 stderr 原文
- 共享模型/关系/口径修改后跑全量;单题/单域修改后至少跑受影响题目

## 6. 问数闭环

```bash
cd <语义项目目录>
wren context instructions                 # 每个新查询会话首次执行
wren memory fetch -q "<问题>"
wren memory recall -q "<问题>" --limit 3
wren dry-plan --sql "<SQL>"               # 规划校验, 失败先改 SQL
wren query --sql "<SQL>" -o csv -q        # 实际执行
wren memory store --nl "<问题>" --sql "<已验证SQL>"   # 结果确认后入库
```

存储后必须检查文件内容、能否召回,不能只看调用成功。

## 已知陷阱

- 部分引擎版本存在 DECIMAL 规划退化,精确金额列以 DOUBLE 绕行时要在文档记录,
  且不要推广到所有业务
- 视图内不做列算术,算术放查询层或 cube 度量
- 生成器与快照的行尾要一致(统一 LF),否则重跑出现全量幻影 diff
