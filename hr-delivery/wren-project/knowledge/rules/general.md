# 星辰科技 HR 业务口径 (Business Rules)

## 数据范围与时间口径
- 数据截至 **2026-08-31**。问题中的"目前/当前/现在"均指该快照日期。
- 员工档案: 2020-06 公司成立起全量; 薪资发放(`salary_payments`): 2023-01 起; 绩效(`performance_reviews`): 2023H1 起; 考勤(`attendance_records`): 2025-01-01 起; 招聘(`job_openings`/`candidates`): 2023 年起。
- 月度区间统一用 `salary_payments.pay_period`（格式 `YYYY-MM`）过滤；日期区间用对应事实表日期列。

## 人员口径
- **在职员工**: `employees.status = '在职'`（等价于 `termination_date IS NULL`）。离职员工档案保留在表中，统计"当前人数"必须带在职过滤。
- **离职率 (官方口径)**: 期间离职人数 ÷ 期初在职人数。
  - 期间离职人数: `termination_date` 落在期间内的员工数（含主动与被动，含实习/外包）。
  - 期初在职人数: `hire_date < 期间开始日` 且（`termination_date IS NULL` 或 `termination_date >= 期间开始日`）。
  - 年离职率直接用年离职人数/年初人数（不做年化折算）；跨年比较时按自然年。
- **司龄**: `tenure_years`（计算列，(current_date − hire_date)/365.25）。**年龄**: `age`（计算列，按出生年与当前年差）。
- 组织架构为一级扁平部门（9 个），无部门层级汇总；`employees.dept_id` 为员工**当前**部门。

## 薪酬与成本口径
- 所有金额单位为**人民币元/月**，均为**税前**，另有说明除外。
- **人力成本** = `gross_pay`（应发合计 = base_pay + overtime_pay + bonus），来自 `v_monthly_salary` 或 `salary_payments` 计算列。实发到手用 `net_pay`。
- `employees.base_salary` 是**当前**基本工资（快照）；**历史**月薪要看 `salary_payments.base_pay`（含年度调薪）。
- **年终奖在每年 1 月发放**（对应上一年度），因此 1 月人力成本存在季节性峰值，分析月度成本趋势时应说明或剔除奖金。
- 员工平均工资: 在职员工 `base_salary` 平均值；人均人力成本: 期间 gross_cost ÷ 期间发薪月人数。

## 考勤与假期口径
- 考勤仅覆盖工作日（周末无记录）。出勤率 = 状态为"正常/远程办公/请假"的比例按需定义，**默认出勤 = 状态 IN ('正常','远程办公')**。
- 加班分析用 `attendance_records.overtime_hours`（0.5h 粒度）。
- 请假天数默认统计 **status='已批准'** 的请假单；引用时注明口径。
- "迟到" `status='迟到'`；"旷工" `status='旷工'`。

## 招聘口径
- 漏斗: 投递(`candidates.applied_at`) → 面试(`interviews`) → offer(`stage='已发offer'`) → 入职(`stage='已入职'`, 关联 `hired_emp_id`)。
- 转化率 = 该阶段达成人数 ÷ 上一阶段人数；"招聘完成率" = 实际入职人数 ÷ 计划 headcount（`job_openings`）。
- 候选人来源: 内推/招聘网站/猎头/校园招聘，中文原值等值查询。
- **Offer 接受率** = `offers.status='已接受'` 的 offer 数 ÷ 全部已回复 offer（已接受+已拒绝；"待回复"不计入分母）。按发放年份（`offer_date`）统计。
- **单人招聘成本** = 期间招聘渠道费用合计（`recruitment_costs`，含内推奖励）÷ 期间新入职人数（`employees.hire_date` 在期间内的新员工数）。
- **渠道人均成本** = 该渠道费用 ÷ 该渠道入职人数（入职员工来源以 `candidates.source` 为准）。

## v2 扩展域口径
- **编制达成率** = 年末实际在职人数 ÷ 计划编制（`v_headcount_vs_plan`：planned_headcount vs actual_headcount），按部门×年度统计；实际 = hire_date ≤ 年末 且未在年末前离职。
- **人力总成本** = 税前应发（base_pay+overtime_pay+bonus）+ 社保公积金企业缴纳（`insurance_payments.company_total`）。可用 `v_workforce_monthly` 视图分项聚合后相加。
- **人均人力成本** = 期间人力总成本 ÷ 期间发薪人次（或月均在职人数，引用时注明分母口径）。
- **调薪渗透率** = 期间有调薪记录（`salary_changes`）的去重员工数 ÷ 期间期初在职人数。**调薪幅度** 用 `change_pct`（百分比）。
- **晋升率** = 期间晋升人数（`promotions` 去重）÷ 期初在职人数；晋升调薪幅度看 `promotions.raise_pct`。
- **加班审批通过率** = `overtime_requests.status='已批准'` 的加班单 ÷ 全部加班单；**调休补偿占比** = compensation='调休' 的已批准加班单 ÷ 已批准加班单。
- **年假使用率** = 截至期末已使用年假 ÷ 当年年度额度（`leave_balances`: used/entitled，type='年假'）；`expired` 为年度清零损失。
- **合同到期预警** = `contracts.is_expiring_soon`（计算列：履行中且 end_date ≤ 当前+90天）；"无固定期限"合同 end_date 为空。
- **目标完成率** = `performance_goals.completion_pct` 按 weight 加权平均（同员工同周期权重和为100）；与 `performance_reviews.grade` 同周期关联分析。
- **敬业度** = `engagement_surveys.engagement_score`（1-5，年度）；低敬业度阈值 < 3.0。
- **主动离职率 / 被动离职率** = 官方离职率口径下按 `is_voluntary` 拆分：主动 = is_voluntary=true；被动（辞退等）= false。
- **真实离职原因 vs 档案原因** = `exit_interviews.real_reason_category`（面谈归类）对比 `employees.termination_reason`（档案原因），两者常不一致属正常现象。
- **关键岗位继任覆盖率** = 有继任者（`talent_pool.pool_type='继任者'` 且 status='在池'）的部门总监岗位数 ÷ 总监岗位总数。
- **人效指数** = 期间人力总成本 ÷ 期间在职人数月均（简化人效口径，公司无营收数据）。

## 查询注意事项
- 枚举值均为中文原值（如 '在职'、'技术部'、'年假'、'S'），WHERE 条件直接使用中文等值，不要翻译成英文。
- 绩效等级 S 最好 D 最差；C/D 视为低绩效。
- 员工数 count 用 `emp_id`；薪资聚合前确认粒度（`salary_payments` 一人一月一行）。
