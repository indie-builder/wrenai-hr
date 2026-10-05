---
name: wren-hr
description: wrenai-hr 仓库的 HR 业务领域包——星辰科技演示数据的口径地图、验证入口与问数纪律。在本仓库做 HR 问数、改模型、跑回归、维护仪表盘时使用;机制层见 semantic-analytics 技能。
---

在本仓库做 HR 分析交付时使用。领域数据、模型、口径、题库都在仓库内,
本技能只做地图与纪律;**运行时细节(数据库引擎、重建入口、连接 profile)
以仓库 `AGENTS.md` 的当前约定为唯一权威——引擎在迁移期会切换,本技能不复制
这些会过时的事实**。口径同样以 `knowledge/rules/general.md` 为唯一权威,
不要凭记忆回答。

## 内容地图

```text
hr-demo/
├── db/                          # 造数脚本 + 确定性种子 + DuckDB 重建入口(见 AGENTS.md)
├── wren-project/
│   ├── models/*/metadata.yml    # 25 模型, 列描述是 NL2SQL 功能输入
│   ├── relationships.yml        # 32 条关系
│   ├── views/ + cubes/          # 复用投影与聚合 (6 视图 + 6 cube)
│   ├── knowledge/rules/general.md   # ★ 业务口径唯一权威, 先读这个
│   └── apps/hr-overview/        # 仪表盘 (裁剪 MDL + 12 表 Parquet 快照)
└── validation/v2/               # 41 题双路径回归: questions.py + run_all.py
```

## 问数操作序列

在语义项目目录(`hr-demo/wren-project`)的**子 Shell** 中执行(避免改变
后续命令工作目录),六步:`context instructions`(每会话首次)→
`memory fetch` → `memory recall --limit 3` → `dry-plan` → `query -o csv -q` →
结果确认后 `memory store` 并验证能召回。

- SQL 用 MDL 模型名;直查物理表仅用于标准答案核对或数据诊断
- 缺少 profile/环境配置时按 AGENTS.md 的重建步骤操作,不在输出中暴露凭据

## 高频易混口径(详见 knowledge/rules/general.md)

- 在职 = `status='在职'`;历史期初人数按入职/离职日期还原,不能只筛当前在职
- 离职率 = 期间离职 ÷ 期初在职,含主动/被动/实习/外包
- `employees.dept_id` 是**当前档案部门**,按它算历史部门指标要说明未还原调岗
- 应发口径 ≠ 含社保公积金企业缴纳的人力总成本,两者必须区分
- Offer 接受率 = 已接受 ÷ (已接受 + 已拒绝),排除待回复
- 数据快照日 2026-08-31;时间敏感题先核查 SQL 中的 `current_date` 与快照日差异

## 验证与仪表盘

```bash
# 仓库根目录
python3 hr-demo/validation/v2/run_all.py --only q03          # 单题
python3 hr-demo/validation/v2/run_all.py --domain 人效分析    # 单域
python3 hr-demo/validation/v2/run_all.py                     # 全量
```

- 每次运行覆盖 `summary.csv` 与 `results/qXX.*.csv`;共享口径改动后跑全量
- 仪表盘新检出、数据或模型更新后，按 [快照流程](../../../hr-demo/README.md#仪表盘快照与本地预览) 先构建、导出并检查，再启动预览；核对页面数字。
- 不为通过测试修改正确业务口径;报告通过率时区分历史记录与本次实际执行
