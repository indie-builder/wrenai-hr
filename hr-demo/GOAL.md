# 目标：HR 业务场景全维度数据补齐 → 语义层完善 → 端到端验证（一体化交付）

<!-- docs:historical -->

本文是历史目标与验收记录，保留当时的规划名称和命令。当前操作见 [演示 README](README.md) 与 [验证入口](validation/v2/README.md)。

- 版本: v2.0（基线 = 已完成的 Phase 1 交付，见 `README.md`）
- 状态: 历史 v2 验收记录（2026-09-07）；本文保留原目标，当前实现、命令与验证范围以 `README.md` 和 `validation/v2/matrix_v2.md` 为准。固定 SQL 通过不代表自然语言生成准确率。
- 数据快照基准日: 2026-08-31（不变，全部新增数据保持同一时间轴）

---

## 1. 目标陈述（一句话）

在现有 11 张表、9 个部门、786 名员工、12 题验证的基线上，**补齐 HR 八大业务域缺失的维度数据**，
经由本项目（数据生成器 → Wren MDL 语义层 → 治理口径 → 语义记忆 → GenBI 仪表盘）**一体化生成落地**，
并将**端到端验证扩展为覆盖全部业务域、可一键回归的统一验证体系**，最终达成：
**数据域全覆盖、语义层零缺口、验证矩阵 ≥36 题且口径题 100% 通过、仪表盘与语义层数字一致**。

## 2. 现状基线（Phase 1 已交付）

| 维度 | 现状 |
|---|---|
| 数据层 | 11 张表：departments / employees / salary_payments / attendance_records / leave_requests / job_openings / candidates / interviews / performance_reviews / training_records / transfers，共 22.6 万行 |
| 语义层 | 11 模型 + 17 关系 + 3 视图 + 3 cube + 口径规则 + 词汇表 + 语义记忆（LanceDB） |
| 验证 | 12 题双路径比对 12/12 PASS（详见 `validation/matrix.md`） |
| 仪表盘 | hr-overview 单页（5 KPI + 6 图），浏览器端 wasm 语义引擎 |

**已知缺口**：招聘只有漏斗无 offer/渠道成本；薪酬无调薪与社保明细；考勤无加班审批与假期余额；
绩效无目标管理；员工生命周期缺晋升/合同；员工关系（敬业度/离职面谈）与人效分析完全缺失。

## 3. 范围：HR 八大业务域补齐清单

> 约定：`(有)` = 已有表，`(新)` = 本目标新增表。所有新表与现有表保持外键闭环与时间轴一致（2023-01 起，截至 2026-08-31）。

### D1 组织与编制
- `headcount_plan` (新)：年度部门编制规划（计划编制、预算人力成本、年度）→ 支撑 **编制达成率**、**人力成本预算 vs 实际**
- departments (有)：补充部门负责人、成本中心字段

### D2 员工全生命周期
- `promotions` (新)：晋升记录（晋升前后职级/职位/薪资、日期）→ **晋升率、晋升周期**
- `contracts` (新)：劳动合同（类型、起止、续签次数、到期日、状态）→ **合同到期预警、续签率**
- employees / transfers (有)

### D3 薪酬福利
- `salary_changes` (新)：调薪记录（调薪前后、幅度、类型：年度调薪/晋升调薪/特批）→ **调薪渗透率、调薪幅度分布**
- `insurance_payments` (新)：社保公积金月度企业缴纳（养老/医疗/失业/工伤/生育/公积金分项）→ **企业用人总成本（含隐形）**
- `awards_penalties` (新)：奖惩记录（类型、金额、事由）→ **奖惩分布**
- salary_payments (有)

### D4 考勤假期
- `overtime_requests` (新)：加班申请与审批（申请时长/实际时长/审批状态/补偿方式：调休或加班费）→ **加班审批合规率、调休偿还率**
- `leave_balances` (新)：年假/调休余额快照（每人每季度）→ **假期使用率、失效损失**
- attendance_records / leave_requests (有)

### D5 招聘用工
- `offers` (新)：offer 记录（发放/接受/拒绝及理由、薪酬包）→ **offer 接受率、拒绝原因分析**
- `recruitment_costs` (新)：渠道费用（渠道、月份、费用）→ **单人招聘成本、渠道 ROI**
- job_openings / candidates / interviews (有)：candidates 补充期望到岗时间、失败阶段归因

### D6 绩效发展
- `performance_goals` (新)：半年度目标（KPI/OKR、权重、完成率）→ **目标达成率分布、与绩效等级相关性**
- `talent_pool` (新)：高潜/继任名单（潜力评级、继任岗位）→ **关键岗位继任覆盖率**
- performance_reviews / training_records (有)

### D7 员工关系
- `engagement_surveys` (新)：敬业度调研（年度、每人各维度得分 1-5）→ **敬业度趋势与流失关联**
- `exit_interviews` (新)：离职面谈（离职原因归类、满意度、是否愿推荐）→ **真实流失原因 vs 档案原因交叉验证**

### D8 人效与成本分析（分析层，不建新事实表）
- 语义层视图 + cube 承载：**人均人力成本、人均培训时长、编制达成率、离职率(总/主动/被动)、人效指数**
- 依赖 D1-D7 新表 + 现有事实表组合计算

**数据层交付口径：新增 14 张表（11 → 25），新增行数预估 3~5 万行，全部由 `db/seed/gen_hr_data.py` v2 按固定种子生成，保持既有业务规律一致性（如：敬业度低的员工离职概率更高、offer 拒绝率与薪酬竞争力负相关、高潜不进 PIP 等）。**

## 4. 范围：语义层完善清单

1. **模型**：12 张新表全部建 MDL 模型（继承 db COMMENT 的中文描述），新增关系 ≥15 条（含与 employees/departments 的闭环），validate 零错误
2. **计算列**：如 `contracts.is_expiring_soon`（90 天内到期）、`promotions.raise_pct`、`overtime_requests.approval_gap` 等
3. **视图**：新增 `v_workforce_analytics`（人效月度宽表）、`v_attrition_detail`（离职明细宽表，含面谈/敬业度关联）等 ≥2 个
4. **Cube**：新增 `attrition`（离职分析）、`headcount_vs_plan`（编制达成）、扩展 `labor_cost`（含社保企业成本）
5. **口径规则** `knowledge/rules/general.md` v2：新域口径 ≥10 条（调薪渗透率、offer 接受率、编制达成率、假期使用率、主动/被动离职率、单人招聘成本、敬业度算法、继任覆盖率、人效指数、人力总成本口径）
6. **词汇表** v2：新增业务词 ≥15 条
7. **语义记忆**：每个验证通过的 NL→SQL 对即时 store，本阶段结束后 memory 条目 ≥60

## 5. 范围：端到端验证整合（本目标的核心整合点）

验证不再是收尾动作，而是与数据、语义层同步设计的统一体系：

1. **题库扩容**：12 → **≥36 题**，八大域每域 ≥3 题（含 1 道口径题 + 1 道跨表 join 题 + 1 道趋势/排行题），旧 12 题原样保留作回归
2. **双路径比对法不变**：A 路径 `groundtruth.sql` v2 直连物理表；B 路径 agent 基于 MDL/规则/记忆写 SQL 经 `wren dry-plan + query` 执行；逐值比对
3. **判定标准（DoD 硬指标）**：
   - P0 口径题（涉及业务定义的题）：**100% PASS，零口径错误**（口径错误 = 虽然数字能算出来但违反 rules 定义）
   - 总体通过率 **≥95%**，失败题必须归因（数据问题/口径问题/SQL 问题）并修复回归
   - 旧 12 题回归 **100% PASS**
4. **仪表盘一致性抽查**：仪表盘 ≥5 个数字与验证矩阵 B 路径结果一致（含新增域图表）
5. **一键回归**：新增 `validation/run_all.sh`——从零跑通 GT 生成 → Wren 路径 → 自动 diff → 输出 `matrix_v2.md` + PASS/FAIL 汇总 CSV；支持单题重跑
6. **诚实性纪律**：验证过程中发现的任何数据/口径/引擎问题，如实记录到 matrix 的"被拦下的问题"清单，修复后回归，不允许为通过而改口径

## 6. 非目标（明确排除）

- 不接入真实 HR 系统/真实个人数据（保持仿真数据）
- 不做行级/列级权限（RLAC/CLAC，另立目标）
- 不做公网部署（Vercel/Cloudflare 需用户账号 token，本地预览为准）
- 原阶段不更换数据源；此条已由 2026-09-26 的 DuckDB 迁移取代，当前项目不再依赖 PostgreSQL。

## 7. 里程碑

| 阶段 | 内容 | 交付物 | 验收 |
|---|---|---|---|
| M1 数据补齐 | 生成器 v2 + 14 新表 schema + 装载 | `db/schema_v2.sql`、`gen_hr_data.py` v2、行数报告 | 23 张表外键闭环、时间轴一致、业务规律抽检合理 |
| M2 语义层 | 模型/关系/计算列/视图/cube/口径/词汇 | validate+build 通过、memory index v2 | 零错误；新域全部可查询 |
| M3 验证整合 | 题库 v2 + GT v2 + run_all.sh 自动比对 | `validation/matrix_v2.md` + 汇总 CSV | DoD 硬指标全绿 |
| M4 仪表盘与文档 | 新增 ≥3 个域的仪表盘页签；README/matrix/glossary 同步 | apps v2 + 文档 v2 | 一致性抽查通过、一键复现跑通 |

## 8. 最终验收清单（Definition of Done）

- [x] 数据：25 张表、八大域全覆盖、22.6 万 → 25.3 万行、固定种子可复现（db/seed/gen_hr_data*.py）
- [x] 语义层：`wren context validate` 零错误；模型 25、关系 32、视图 6、cube 6；口径规则 v2 新增 17 条
- [x] 验证：题库 41（≥36）；P0 口径题 11/11=100%、总体 41/41=100%（≥95%）、基线 13 题回归 100%；唯一失败题 q17 已归因（引擎 DECIMAL/DOUBLE 下推缺陷）并修复回归
- [x] 自动化：`validation/v2/run_all.py` 一键完成 GT→Wren→比对→报告（summary.csv + results/）
- [x] 仪表盘：新增 4 KPI + 6 图表（编制/成本构成/拒绝原因/渠道成本/离职归因/目标完成率），9 项数字与语义层一致
- [x] 文档：README / GOAL / matrix_v2 / glossary 全部同步至 v2 状态
