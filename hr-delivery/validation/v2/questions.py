#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端验证题库 v2 (GOAL.md M3)
- 每题两条独立路径: gt = 直连 PostgreSQL 物理表 (标准答案); wren = 基于 MDL/视图/口径经语义层执行
- 别名两侧完全一致, 由 run_all.py 自动逐值比对
- priority: P0 = 口径题(零口径错误要求) / P1 = 常规
"""

QUESTIONS = [
# ============ 基线回归 (Phase 1 原题) ============
dict(id="q01", domain="人员基础", priority="P1", question="目前公司在职员工总人数是多少？",
gt="""SELECT count(*) AS 在职人数 FROM employees WHERE status = '在职'""",
wren="""SELECT count(*) AS 在职人数 FROM employees WHERE status = '在职'"""),

dict(id="q02", domain="人员基础", priority="P1", question="各部门在职人数分布（从高到低）？",
gt="""SELECT d.dept_name AS 部门, count(*) AS 在职人数
FROM employees e JOIN departments d ON e.dept_id = d.dept_id
WHERE e.status = '在职' GROUP BY d.dept_name""",
wren="""SELECT dept_name AS 部门, count(*) AS 在职人数 FROM v_active_employees GROUP BY dept_name"""),

dict(id="q03", domain="人员基础", priority="P0", question="2025年公司整体离职率（官方口径）？",
gt="""WITH leavers AS (SELECT count(*) AS n FROM employees WHERE termination_date >= DATE '2025-01-01' AND termination_date < DATE '2026-01-01'),
hc AS (SELECT count(*) AS n FROM employees WHERE hire_date < DATE '2025-01-01' AND (termination_date IS NULL OR termination_date >= DATE '2025-01-01'))
SELECT l.n AS 离职人数, h.n AS 期初在职, round(l.n * 100.0 / h.n, 2) AS 离职率百分比 FROM leavers l, hc h""",
wren="""WITH leavers AS (SELECT count(*) AS n FROM employees WHERE termination_date >= DATE '2025-01-01' AND termination_date < DATE '2026-01-01'),
hc AS (SELECT count(*) AS n FROM employees WHERE hire_date < DATE '2025-01-01' AND (termination_date IS NULL OR termination_date >= DATE '2025-01-01'))
SELECT l.n AS 离职人数, h.n AS 期初在职, round(l.n * 100.0 / h.n, 2) AS 离职率百分比 FROM leavers l, hc h"""),

dict(id="q04", domain="人员基础", priority="P0", question="2025年各部门离职率排行？",
gt="""WITH leavers AS (SELECT dept_id, count(*) AS l FROM employees WHERE termination_date >= DATE '2025-01-01' AND termination_date < DATE '2026-01-01' GROUP BY dept_id),
hc AS (SELECT dept_id, count(*) AS h FROM employees WHERE hire_date < DATE '2025-01-01' AND (termination_date IS NULL OR termination_date >= DATE '2025-01-01') GROUP BY dept_id)
SELECT d.dept_name AS 部门, coalesce(l.l,0) AS 离职人数, h.h AS 期初在职, round(coalesce(l.l,0) * 100.0 / h.h, 2) AS 离职率百分比
FROM hc h JOIN departments d ON d.dept_id = h.dept_id LEFT JOIN leavers l ON l.dept_id = h.dept_id""",
wren="""WITH leavers AS (SELECT e.dept_id, count(*) AS l FROM employees e WHERE e.termination_date >= DATE '2025-01-01' AND e.termination_date < DATE '2026-01-01' GROUP BY e.dept_id),
hc AS (SELECT e.dept_id, count(*) AS h FROM employees e WHERE e.hire_date < DATE '2025-01-01' AND (e.termination_date IS NULL OR e.termination_date >= DATE '2025-01-01') GROUP BY e.dept_id)
SELECT d.dept_name AS 部门, coalesce(l.l, 0) AS 离职人数, h.h AS 期初在职, round(coalesce(l.l, 0) * 100.0 / h.h, 2) AS 离职率百分比
FROM hc h JOIN departments d ON d.dept_id = h.dept_id LEFT JOIN leavers l ON l.dept_id = h.dept_id"""),

dict(id="q05", domain="人员基础", priority="P0", question="2026上半年月度人力成本趋势（应发合计）？",
gt="""SELECT pay_period AS 月份, round(sum(base_pay + overtime_pay + bonus), 2) AS 人力成本应发, count(*) AS 发薪人次
FROM salary_payments WHERE pay_period BETWEEN '2026-01' AND '2026-06' GROUP BY pay_period""",
wren="""SELECT pay_period AS 月份, round(sum(base_pay + overtime_pay + bonus), 2) AS 人力成本应发, count(*) AS 发薪人次
FROM v_monthly_salary WHERE pay_period BETWEEN '2026-01' AND '2026-06' GROUP BY pay_period"""),

dict(id="q06", domain="人员基础", priority="P1", question="各职级在职员工平均基本工资？",
gt="""SELECT job_level AS 职级, count(*) AS 人数, round(avg(base_salary), 0) AS 平均基本工资
FROM employees WHERE status = '在职' GROUP BY job_level""",
wren="""SELECT job_level AS 职级, count(*) AS 人数, round(avg(base_salary), 0) AS 平均基本工资
FROM employees WHERE status = '在职' GROUP BY job_level"""),

dict(id="q07a", domain="人员基础", priority="P1", question="2026年新入职员工总人数？",
gt="""SELECT count(*) AS 新入职总人数 FROM employees WHERE hire_date BETWEEN '2026-01-01' AND '2026-08-31'""",
wren="""SELECT count(*) AS 新入职总人数 FROM employees WHERE hire_date BETWEEN '2026-01-01' AND '2026-08-31'"""),

dict(id="q07b", domain="人员基础", priority="P1", question="2026年新入职员工的招聘来源分布？",
gt="""SELECT c.source AS 来源, count(*) AS 人数 FROM candidates c
JOIN employees e ON c.hired_emp_id = e.emp_id
WHERE e.hire_date BETWEEN '2026-01-01' AND '2026-08-31' GROUP BY c.source""",
wren="""SELECT c.source AS 来源, count(*) AS 人数 FROM candidates c
JOIN employees e ON c.hired_emp_id = e.emp_id
WHERE e.hire_date BETWEEN '2026-01-01' AND '2026-08-31' GROUP BY c.source"""),

dict(id="q08", domain="人员基础", priority="P1", question="2025年各类假期已批准总天数？",
gt="""SELECT leave_type AS 假期类型, sum(days) AS 总天数, count(*) AS 单数 FROM leave_requests
WHERE status = '已批准' AND start_date BETWEEN '2025-01-01' AND '2025-12-31' GROUP BY leave_type""",
wren="""SELECT leave_type AS 假期类型, sum(days) AS 总天数, count(*) AS 单数 FROM leave_requests
WHERE status = '已批准' AND start_date BETWEEN '2025-01-01' AND '2025-12-31' GROUP BY leave_type"""),

dict(id="q09", domain="人员基础", priority="P1", question="2025年招聘漏斗：投递→入职转化率？",
gt="""WITH funnel AS (SELECT count(*) AS applied, count(*) FILTER (WHERE stage = '已入职') AS hired
FROM candidates WHERE applied_at BETWEEN '2025-01-01' AND '2025-12-31')
SELECT applied AS 投递人数, hired AS 入职人数, round(hired * 100.0 / applied, 2) AS 转化率百分比 FROM funnel""",
wren="""WITH funnel AS (SELECT count(*) AS applied, count(*) FILTER (WHERE stage = '已入职') AS hired
FROM candidates WHERE applied_at BETWEEN '2025-01-01' AND '2025-12-31')
SELECT applied AS 投递人数, hired AS 入职人数, round(hired * 100.0 / applied, 2) AS 转化率百分比 FROM funnel"""),

dict(id="q10", domain="人员基础", priority="P1", question="在职员工性别比例与平均工资？",
gt="""SELECT gender AS 性别, count(*) AS 人数, round(count(*) * 100.0 / sum(count(*)) OVER (), 2) AS 占比,
round(avg(base_salary), 0) AS 平均基本工资 FROM employees WHERE status = '在职' GROUP BY gender""",
wren="""SELECT gender AS 性别, count(*) AS 人数, round(count(*) * 100.0 / sum(count(*)) OVER (), 2) AS 占比,
round(avg(base_salary), 0) AS 平均基本工资 FROM employees WHERE status = '在职' GROUP BY gender"""),

dict(id="q11", domain="人员基础", priority="P1", question="2026H1人均月加班前3部门？",
gt="""WITH ot AS (SELECT d.dept_name, sum(a.overtime_hours) AS total_ot, count(DISTINCT a.emp_id) AS headcount
FROM attendance_records a JOIN employees e ON a.emp_id = e.emp_id JOIN departments d ON e.dept_id = d.dept_id
WHERE a.att_date BETWEEN '2026-01-01' AND '2026-06-30' GROUP BY d.dept_name)
SELECT dept_name AS 部门, round(total_ot, 0) AS 加班总时长, round(total_ot / (headcount * 6.0), 2) AS 人均月加班小时
FROM ot ORDER BY 人均月加班小时 DESC LIMIT 3""",
wren="""WITH ot AS (SELECT d.dept_name, sum(a.overtime_hours) AS total_ot, count(DISTINCT a.emp_id) AS headcount
FROM attendance_records a JOIN employees e ON a.emp_id = e.emp_id JOIN departments d ON e.dept_id = d.dept_id
WHERE a.att_date BETWEEN '2026-01-01' AND '2026-06-30' GROUP BY d.dept_name)
SELECT dept_name AS 部门, round(total_ot, 0) AS 加班总时长, round(total_ot / (headcount * 6.0), 2) AS 人均月加班小时
FROM ot ORDER BY 人均月加班小时 DESC LIMIT 3"""),

dict(id="q12", domain="人员基础", priority="P1", question="2026H1绩效C/D在职员工的部门分布Top5？",
gt="""SELECT d.dept_name AS 部门, count(*) AS 低绩效人数 FROM performance_reviews pr
JOIN employees e ON pr.emp_id = e.emp_id JOIN departments d ON e.dept_id = d.dept_id
WHERE pr.review_period = '2026H1' AND pr.grade IN ('C','D') AND e.status = '在职' GROUP BY d.dept_name LIMIT 5""",
wren="""SELECT d.dept_name AS 部门, count(*) AS 低绩效人数 FROM performance_reviews pr
JOIN employees e ON pr.emp_id = e.emp_id JOIN departments d ON e.dept_id = d.dept_id
WHERE pr.review_period = '2026H1' AND pr.grade IN ('C','D') AND e.status = '在职' GROUP BY d.dept_name LIMIT 5"""),

# ============ D1 组织与编制 ============
dict(id="q13", domain="组织与编制", priority="P0", question="2025年各部门编制达成率（计划 vs 年末实际）？",
gt="""WITH plan AS (SELECT dept_id, planned_headcount FROM headcount_plan WHERE plan_year = 2025),
actual AS (SELECT dept_id, count(*) AS n FROM employees WHERE hire_date <= DATE '2025-12-31'
AND (termination_date IS NULL OR termination_date > DATE '2025-12-31') GROUP BY dept_id)
SELECT d.dept_name AS 部门, p.planned_headcount AS 计划编制, a.n AS 年末实际,
round(a.n * 100.0 / p.planned_headcount, 1) AS 编制达成率
FROM plan p JOIN actual a ON a.dept_id = p.dept_id JOIN departments d ON d.dept_id = p.dept_id""",
wren="""SELECT dept_name AS 部门, planned_headcount AS 计划编制, actual_headcount AS 年末实际,
round(actual_headcount * 100.0 / planned_headcount, 1) AS 编制达成率
FROM v_headcount_vs_plan WHERE plan_year = 2025"""),

dict(id="q14", domain="组织与编制", priority="P1", question="2025年各部门人力成本预算 vs 实际应发？",
gt="""SELECT d.dept_name AS 部门, hp.budget_labor_cost AS 预算人力成本,
round((SELECT coalesce(sum(s.base_pay + s.overtime_pay + s.bonus), 0) FROM salary_payments s
JOIN employees e ON e.emp_id = s.emp_id WHERE e.dept_id = d.dept_id AND s.pay_period LIKE '2025%'), 2) AS 实际应发成本
FROM headcount_plan hp JOIN departments d ON d.dept_id = hp.dept_id WHERE hp.plan_year = 2025""",
wren="""SELECT d.dept_name AS 部门, hp.budget_labor_cost AS 预算人力成本,
round(sum(v.base_pay + v.overtime_pay + v.bonus), 2) AS 实际应发成本
FROM headcount_plan hp JOIN departments d ON d.dept_id = hp.dept_id
JOIN v_workforce_monthly v ON v.dept_name = d.dept_name AND v.pay_period LIKE '2025%'
WHERE hp.plan_year = 2025 GROUP BY d.dept_name, hp.budget_labor_cost"""),

dict(id="q15", domain="组织与编制", priority="P1", question="2026年计划编制总数与当前在职人数对比？",
gt="""SELECT (SELECT sum(planned_headcount) FROM headcount_plan WHERE plan_year = 2026) AS 计划编制总数,
(SELECT count(*) FROM employees WHERE status = '在职') AS 当前在职人数""",
wren="""SELECT (SELECT sum(planned_headcount) FROM headcount_plan WHERE plan_year = 2026) AS 计划编制总数,
(SELECT count(*) FROM employees WHERE status = '在职') AS 当前在职人数"""),

# ============ D2 员工生命周期 ============
dict(id="q16", domain="员工生命周期", priority="P1", question="2024年以来晋升人次与晋升率（期初2024-01-01）？",
gt="""WITH p AS (SELECT count(*) AS n FROM promotions WHERE promo_date >= DATE '2024-01-01'),
hc AS (SELECT count(*) AS n FROM employees WHERE hire_date < DATE '2024-01-01' AND (termination_date IS NULL OR termination_date >= DATE '2024-01-01'))
SELECT p.n AS 晋升人次, hc.n AS 期初在职, round(p.n * 100.0 / hc.n, 2) AS 晋升率 FROM p, hc""",
wren="""WITH p AS (SELECT count(*) AS n FROM promotions WHERE promo_date >= DATE '2024-01-01'),
hc AS (SELECT count(*) AS n FROM employees WHERE hire_date < DATE '2024-01-01' AND (termination_date IS NULL OR termination_date >= DATE '2024-01-01'))
SELECT p.n AS 晋升人次, hc.n AS 期初在职, round(p.n * 100.0 / hc.n, 2) AS 晋升率 FROM p, hc"""),

dict(id="q17", domain="员工生命周期", priority="P1", question="各晋升后职级的平均调薪幅度？",
gt="""SELECT to_level AS 晋升后职级, count(*) AS 人次,
round(avg((salary_after - salary_before) * 100.0 / salary_before), 2) AS 平均调薪幅度
FROM promotions GROUP BY to_level""",
wren="""SELECT to_level AS 晋升后职级, count(*) AS 人次,
round(avg((salary_after - salary_before) * 100.0 / salary_before), 2) AS 平均调薪幅度 FROM promotions GROUP BY to_level"""),
# 注: raise_pct 计算列因 wren-core 0.7.6 将 DOUBLE 表达式下推为 PG round(double,int) 而不可用, 改查询时计算

dict(id="q18", domain="员工生命周期", priority="P1", question="90天内到期的履行中合同数与无固定期限在履约合同数？",
gt="""SELECT (SELECT count(*) FROM contracts WHERE status = '履行中' AND end_date IS NOT NULL AND end_date <= current_date + 90) AS 即将到期合同,
(SELECT count(*) FROM contracts WHERE status = '履行中' AND contract_type = '无固定期限') AS 无固定期限在履约""",
wren="""SELECT count(*) FILTER (WHERE is_expiring_soon) AS 即将到期合同,
count(*) FILTER (WHERE contract_type = '无固定期限') AS 无固定期限在履约
FROM contracts WHERE status = '履行中'"""),

# ============ D3 薪酬福利 ============
dict(id="q19", domain="薪酬福利", priority="P0", question="2025年度调薪渗透率（调薪去重人数/期初在职）？",
gt="""WITH sc AS (SELECT count(DISTINCT emp_id) AS n FROM salary_changes WHERE effective_date BETWEEN DATE '2025-01-01' AND DATE '2025-12-31'),
hc AS (SELECT count(*) AS n FROM employees WHERE hire_date < DATE '2025-01-01' AND (termination_date IS NULL OR termination_date >= DATE '2025-01-01'))
SELECT sc.n AS 调薪人数, hc.n AS 期初在职, round(sc.n * 100.0 / hc.n, 1) AS 调薪渗透率 FROM sc, hc""",
wren="""WITH sc AS (SELECT count(DISTINCT emp_id) AS n FROM salary_changes WHERE effective_date BETWEEN DATE '2025-01-01' AND DATE '2025-12-31'),
hc AS (SELECT count(*) AS n FROM employees WHERE hire_date < DATE '2025-01-01' AND (termination_date IS NULL OR termination_date >= DATE '2025-01-01'))
SELECT sc.n AS 调薪人数, hc.n AS 期初在职, round(sc.n * 100.0 / hc.n, 1) AS 调薪渗透率 FROM sc, hc"""),

dict(id="q20", domain="薪酬福利", priority="P1", question="2026H1社保公积金企业缴纳总额（含养老分项）？",
gt="""SELECT round(sum(company_total), 2) AS 企业社保公积金总额, round(sum(pension), 2) AS 其中养老保险
FROM insurance_payments WHERE pay_period BETWEEN '2026-01' AND '2026-06'""",
wren="""SELECT round(sum(company_total), 2) AS 企业社保公积金总额, round(sum(pension), 2) AS 其中养老保险
FROM insurance_payments WHERE pay_period BETWEEN '2026-01' AND '2026-06'"""),

dict(id="q21", domain="薪酬福利", priority="P0", question="2026H1人力总成本（应发+社保企业缴纳）？",
gt="""WITH g AS (SELECT sum(base_pay + overtime_pay + bonus) AS v FROM salary_payments WHERE pay_period BETWEEN '2026-01' AND '2026-06'),
i AS (SELECT sum(company_total) AS v FROM insurance_payments WHERE pay_period BETWEEN '2026-01' AND '2026-06')
SELECT round(g.v + i.v, 2) AS 人力总成本 FROM g, i""",
wren="""WITH g AS (SELECT sum(base_pay + overtime_pay + bonus) AS v FROM v_workforce_monthly WHERE pay_period BETWEEN '2026-01' AND '2026-06'),
i AS (SELECT sum(insurance_company) AS v FROM v_workforce_monthly WHERE pay_period BETWEEN '2026-01' AND '2026-06')
SELECT round(g.v + i.v, 2) AS 人力总成本 FROM g, i"""),

dict(id="q22", domain="薪酬福利", priority="P1", question="2025年奖励与处罚单数及金额对比？",
gt="""SELECT record_type AS 类型, count(*) AS 单数, round(sum(coalesce(amount, 0)), 2) AS 金额合计
FROM awards_penalties WHERE record_date BETWEEN DATE '2025-01-01' AND DATE '2025-12-31' GROUP BY record_type""",
wren="""SELECT record_type AS 类型, count(*) AS 单数, round(sum(coalesce(amount, 0)), 2) AS 金额合计
FROM awards_penalties WHERE record_date BETWEEN DATE '2025-01-01' AND DATE '2025-12-31' GROUP BY record_type"""),

dict(id="q23", domain="薪酬福利", priority="P1", question="各调薪类型的次数与平均幅度？",
gt="""SELECT change_type AS 调薪类型, count(*) AS 次数, round(avg(change_pct), 2) AS 平均幅度
FROM salary_changes GROUP BY change_type""",
wren="""SELECT change_type AS 调薪类型, count(*) AS 次数, round(avg(change_pct), 2) AS 平均幅度
FROM salary_changes GROUP BY change_type"""),

# ============ D4 考勤假期 ============
dict(id="q24", domain="考勤假期", priority="P0", question="2026H1加班审批通过率？",
gt="""SELECT round(sum(CASE WHEN status = '已批准' THEN 1 ELSE 0 END) * 100.0 / count(*), 1) AS 通过率,
count(*) AS 加班单数 FROM overtime_requests WHERE ot_date BETWEEN DATE '2026-01-01' AND DATE '2026-06-30'""",
wren="""SELECT round(sum(CASE WHEN status = '已批准' THEN 1 ELSE 0 END) * 100.0 / count(*), 1) AS 通过率,
count(*) AS 加班单数 FROM overtime_requests WHERE ot_date BETWEEN DATE '2026-01-01' AND DATE '2026-06-30'"""),

dict(id="q25", domain="考勤假期", priority="P1", question="已批准加班中调休补偿的占比（2026H1）？",
gt="""SELECT round(sum(CASE WHEN compensation = '调休' THEN 1 ELSE 0 END) * 100.0 / count(*), 1) AS 调休补偿占比,
count(*) AS 已批准单数 FROM overtime_requests
WHERE status = '已批准' AND ot_date BETWEEN DATE '2026-01-01' AND DATE '2026-06-30'""",
wren="""SELECT round(sum(CASE WHEN compensation = '调休' THEN 1 ELSE 0 END) * 100.0 / count(*), 1) AS 调休补偿占比,
count(*) AS 已批准单数 FROM overtime_requests
WHERE status = '已批准' AND ot_date BETWEEN DATE '2026-01-01' AND DATE '2026-06-30'"""),

dict(id="q26", domain="考勤假期", priority="P1", question="2026Q2年假使用率（公司整体）？",
gt="""SELECT round(sum(used) * 100.0 / sum(entitled), 1) AS 年假使用率, sum(entitled) AS 总额度, sum(used) AS 已使用
FROM leave_balances WHERE balance_type = '年假' AND as_of_quarter = '2026Q2'""",
wren="""SELECT round(sum(used) * 100.0 / sum(entitled), 1) AS 年假使用率, sum(entitled) AS 总额度, sum(used) AS 已使用
FROM leave_balances WHERE balance_type = '年假' AND as_of_quarter = '2026Q2'"""),

dict(id="q27", domain="考勤假期", priority="P1", question="2025年（Q3快照）年假使用率最高的前3个部门？",
gt="""SELECT d.dept_name AS 部门, round(sum(b.used) * 100.0 / sum(b.entitled), 1) AS 年假使用率
FROM leave_balances b JOIN employees e ON b.emp_id = e.emp_id JOIN departments d ON d.dept_id = e.dept_id
WHERE b.balance_type = '年假' AND b.as_of_quarter = '2025Q3' GROUP BY d.dept_name ORDER BY 年假使用率 DESC LIMIT 3""",
wren="""SELECT d.dept_name AS 部门, round(sum(b.used) * 100.0 / sum(b.entitled), 1) AS 年假使用率
FROM leave_balances b JOIN employees e ON b.emp_id = e.emp_id JOIN departments d ON d.dept_id = e.dept_id
WHERE b.balance_type = '年假' AND b.as_of_quarter = '2025Q3' GROUP BY d.dept_name ORDER BY 年假使用率 DESC LIMIT 3"""),

# ============ D5 招聘用工 ============
dict(id="q28", domain="招聘用工", priority="P0", question="2025年Offer接受率（已接受/已回复）？",
gt="""SELECT count(*) FILTER (WHERE status = '已接受') AS 已接受, count(*) FILTER (WHERE status = '已拒绝') AS 已拒绝,
round(count(*) FILTER (WHERE status = '已接受') * 100.0 / NULLIF(count(*) FILTER (WHERE status IN ('已接受','已拒绝')), 0), 1) AS 接受率
FROM offers WHERE offer_date BETWEEN DATE '2025-01-01' AND DATE '2025-12-31'""",
wren="""SELECT count(*) FILTER (WHERE status = '已接受') AS 已接受, count(*) FILTER (WHERE status = '已拒绝') AS 已拒绝,
round(count(*) FILTER (WHERE status = '已接受') * 100.0 / NULLIF(count(*) FILTER (WHERE status IN ('已接受','已拒绝')), 0), 1) AS 接受率
FROM offers WHERE offer_date BETWEEN DATE '2025-01-01' AND DATE '2025-12-31'"""),

dict(id="q29", domain="招聘用工", priority="P1", question="Offer拒绝原因分布？",
gt="""SELECT reject_reason AS 拒绝原因, count(*) AS 数量 FROM offers WHERE status = '已拒绝' GROUP BY reject_reason""",
wren="""SELECT reject_reason AS 拒绝原因, count(*) AS 数量 FROM offers WHERE status = '已拒绝' GROUP BY reject_reason"""),

dict(id="q30", domain="招聘用工", priority="P1", question="2025年单人招聘成本（渠道费用合计/新入职人数）？",
gt="""WITH c AS (SELECT coalesce(sum(amount), 0) AS v FROM recruitment_costs WHERE cost_month LIKE '2025%'),
h AS (SELECT count(*) AS n FROM employees WHERE hire_date BETWEEN DATE '2025-01-01' AND DATE '2025-12-31')
SELECT c.v AS 招聘费用合计, h.n AS 新入职人数, round(c.v / h.n, 0) AS 单人招聘成本 FROM c, h""",
wren="""WITH c AS (SELECT coalesce(sum(amount), 0) AS v FROM recruitment_costs WHERE cost_month LIKE '2025%'),
h AS (SELECT count(*) AS n FROM employees WHERE hire_date BETWEEN DATE '2025-01-01' AND DATE '2025-12-31')
SELECT c.v AS 招聘费用合计, h.n AS 新入职人数, round(c.v / h.n, 0) AS 单人招聘成本 FROM c, h"""),

# ============ D6 绩效发展 ============
dict(id="q31", domain="绩效发展", priority="P1", question="2026H1各部门绩效目标加权平均完成率？",
gt="""SELECT d.dept_name AS 部门, round(sum(g.completion_pct * g.weight) / sum(g.weight), 1) AS 加权完成率,
count(DISTINCT g.emp_id) AS 员工数
FROM performance_goals g JOIN employees e ON g.emp_id = e.emp_id JOIN departments d ON d.dept_id = e.dept_id
WHERE g.review_period = '2026H1' GROUP BY d.dept_name""",
wren="""SELECT d.dept_name AS 部门, round(sum(g.completion_pct * g.weight) / sum(g.weight), 1) AS 加权完成率,
count(DISTINCT g.emp_id) AS 员工数
FROM performance_goals g JOIN employees e ON g.emp_id = e.emp_id JOIN departments d ON d.dept_id = e.dept_id
WHERE g.review_period = '2026H1' GROUP BY d.dept_name"""),

dict(id="q32", domain="绩效发展", priority="P1", question="不同绩效等级的目标加权完成率对比（2025H2）？",
gt="""SELECT pr.grade AS 绩效等级, round(sum(g.completion_pct * g.weight) / sum(g.weight), 1) AS 加权完成率
FROM performance_goals g JOIN performance_reviews pr ON pr.emp_id = g.emp_id AND pr.review_period = g.review_period
WHERE g.review_period = '2025H2' GROUP BY pr.grade""",
wren="""SELECT pr.grade AS 绩效等级, round(sum(g.completion_pct * g.weight) / sum(g.weight), 1) AS 加权完成率
FROM performance_goals g JOIN performance_reviews pr ON pr.emp_id = g.emp_id AND pr.review_period = g.review_period
WHERE g.review_period = '2025H2' GROUP BY pr.grade"""),

dict(id="q33", domain="绩效发展", priority="P1", question="人才池构成（类型×状态）？",
gt="""SELECT pool_type AS 类型, status AS 状态, count(*) AS 人数 FROM talent_pool GROUP BY pool_type, status""",
wren="""SELECT pool_type AS 类型, status AS 状态, count(*) AS 人数 FROM talent_pool GROUP BY pool_type, status"""),

# ============ D7 员工关系 ============
dict(id="q34", domain="员工关系", priority="P1", question="历年敬业度平均分趋势与参与人数？",
gt="""SELECT survey_year AS 年度, round(avg(engagement_score), 2) AS 平均敬业度, count(*) AS 参与人数
FROM engagement_surveys GROUP BY survey_year""",
wren="""SELECT survey_year AS 年度, round(avg(engagement_score), 2) AS 平均敬业度, count(*) AS 参与人数
FROM engagement_surveys GROUP BY survey_year"""),

dict(id="q35", domain="员工关系", priority="P0", question="2024年以来离职面谈的真实原因Top5？",
gt="""SELECT x.real_reason_category AS 真实原因, count(*) AS 人数 FROM exit_interviews x
JOIN employees e ON x.emp_id = e.emp_id WHERE e.termination_date >= DATE '2024-01-01'
GROUP BY x.real_reason_category LIMIT 5""",
wren="""SELECT x.real_reason_category AS 真实原因, count(*) AS 人数 FROM exit_interviews x
JOIN employees e ON x.emp_id = e.emp_id WHERE e.termination_date >= DATE '2024-01-01'
GROUP BY x.real_reason_category LIMIT 5"""),

dict(id="q36", domain="员工关系", priority="P1", question="2024年调研中低敬业度（<3.0）与正常员工的其后离职比例对比？",
gt="""SELECT CASE WHEN s.engagement_score < 3.0 THEN '低(<3.0)' ELSE '正常(>=3.0)' END AS 敬业度分组,
count(*) AS 人数, round(sum(CASE WHEN e.status = '离职' THEN 1 ELSE 0 END) * 100.0 / count(*), 1) AS 其后离职比例
FROM engagement_surveys s JOIN employees e ON s.emp_id = e.emp_id
WHERE s.survey_year = 2024 GROUP BY 1""",
wren="""SELECT CASE WHEN s.engagement_score < 3.0 THEN '低(<3.0)' ELSE '正常(>=3.0)' END AS 敬业度分组,
count(*) AS 人数, round(sum(CASE WHEN e.status = '离职' THEN 1 ELSE 0 END) * 100.0 / count(*), 1) AS 其后离职比例
FROM engagement_surveys s JOIN employees e ON s.emp_id = e.emp_id
WHERE s.survey_year = 2024 GROUP BY 1"""),

# ============ D8 人效分析 ============
dict(id="q37", domain="人效分析", priority="P0", question="2026H1月均人力总成本（应发+社保，万元）？",
gt="""WITH g AS (SELECT sum(base_pay + overtime_pay + bonus) AS v FROM salary_payments WHERE pay_period BETWEEN '2026-01' AND '2026-06'),
i AS (SELECT sum(company_total) AS v FROM insurance_payments WHERE pay_period BETWEEN '2026-01' AND '2026-06')
SELECT round((g.v + i.v) / 6 / 10000, 1) AS 月均总成本万 FROM g, i""",
wren="""WITH g AS (SELECT sum(base_pay + overtime_pay + bonus) AS v FROM v_workforce_monthly WHERE pay_period BETWEEN '2026-01' AND '2026-06'),
i AS (SELECT sum(insurance_company) AS v FROM v_workforce_monthly WHERE pay_period BETWEEN '2026-01' AND '2026-06')
SELECT round((g.v + i.v) / 6 / 10000, 1) AS 月均总成本万 FROM g, i"""),

dict(id="q38", domain="人效分析", priority="P1", question="2025年人均培训时长与培训完成率？",
gt="""SELECT count(DISTINCT emp_id) AS 参训人数, round(sum(hours), 1) AS 总时长,
round(sum(hours) / count(DISTINCT emp_id), 1) AS 人均时长,
round(sum(CASE WHEN completed THEN 1 ELSE 0 END) * 100.0 / count(*), 1) AS 完成率
FROM training_records WHERE training_date BETWEEN DATE '2025-01-01' AND DATE '2025-12-31'""",
wren="""SELECT count(DISTINCT emp_id) AS 参训人数, round(sum(hours), 1) AS 总时长,
round(sum(hours) / count(DISTINCT emp_id), 1) AS 人均时长,
round(sum(CASE WHEN completed THEN 1 ELSE 0 END) * 100.0 / count(*), 1) AS 完成率
FROM training_records WHERE training_date BETWEEN DATE '2025-01-01' AND DATE '2025-12-31'"""),

dict(id="q39", domain="人效分析", priority="P0", question="2025年主动/被动离职率拆分（官方口径分母）？",
gt="""WITH hc AS (SELECT count(*) AS n FROM employees WHERE hire_date < DATE '2025-01-01' AND (termination_date IS NULL OR termination_date >= DATE '2025-01-01')),
lv AS (SELECT count(*) FILTER (WHERE is_voluntary) AS v, count(*) FILTER (WHERE NOT is_voluntary) AS i, count(*) AS t
FROM employees WHERE termination_date >= DATE '2025-01-01' AND termination_date < DATE '2026-01-01')
SELECT hc.n AS 期初在职, lv.v AS 主动离职人数, lv.i AS 被动离职人数,
round(lv.v * 100.0 / hc.n, 2) AS 主动离职率, round(lv.i * 100.0 / hc.n, 2) AS 被动离职率,
round(lv.t * 100.0 / hc.n, 2) AS 总离职率 FROM hc, lv""",
wren="""WITH hc AS (SELECT count(*) AS n FROM employees WHERE hire_date < DATE '2025-01-01' AND (termination_date IS NULL OR termination_date >= DATE '2025-01-01')),
lv AS (SELECT count(*) FILTER (WHERE is_voluntary) AS v, count(*) FILTER (WHERE NOT is_voluntary) AS i, count(*) AS t
FROM employees WHERE termination_date >= DATE '2025-01-01' AND termination_date < DATE '2026-01-01')
SELECT hc.n AS 期初在职, lv.v AS 主动离职人数, lv.i AS 被动离职人数,
round(lv.v * 100.0 / hc.n, 2) AS 主动离职率, round(lv.i * 100.0 / hc.n, 2) AS 被动离职率,
round(lv.t * 100.0 / hc.n, 2) AS 总离职率 FROM hc, lv"""),

dict(id="q40", domain="人效分析", priority="P1", question="2026H1人均月度人力成本（含社保）最高的前5个部门？",
gt="""SELECT d.dept_name AS 部门,
round((sum(s.base_pay + s.overtime_pay + s.bonus) + sum(i.company_total)) / (count(DISTINCT s.emp_id) * 6.0), 0) AS 人均月成本
FROM salary_payments s JOIN insurance_payments i ON i.emp_id = s.emp_id AND i.pay_period = s.pay_period
JOIN employees e ON e.emp_id = s.emp_id JOIN departments d ON d.dept_id = e.dept_id
WHERE s.pay_period BETWEEN '2026-01' AND '2026-06' GROUP BY d.dept_name ORDER BY 人均月成本 DESC LIMIT 5""",
wren="""SELECT dept_name AS 部门,
round((sum(base_pay + overtime_pay + bonus) + sum(insurance_company)) / (count(DISTINCT emp_id) * 6.0), 0) AS 人均月成本
FROM v_workforce_monthly WHERE pay_period BETWEEN '2026-01' AND '2026-06'
GROUP BY dept_name ORDER BY 人均月成本 DESC LIMIT 5"""),
]
