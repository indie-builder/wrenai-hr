-- ============================================================
-- 端到端验证: 标准答案 (Ground Truth)
-- 路径: 直连 PostgreSQL 物理表计算, 独立于 Wren MDL 语义层
-- 执行: docker exec -i wrenai-hr-pg psql -U hr -d hr_demo < groundtruth.sql
-- ============================================================

\echo '=== Q1 目前在职员工总人数 ==='
SELECT count(*) AS 在职人数 FROM employees WHERE status = '在职';

\echo '=== Q2 各部门在职人数分布 ==='
SELECT d.dept_name AS 部门, count(*) AS 在职人数
FROM employees e JOIN departments d ON e.dept_id = d.dept_id
WHERE e.status = '在职'
GROUP BY d.dept_name ORDER BY 在职人数 DESC;

\echo '=== Q3 2025年整体离职率 (期间离职/期初在职) ==='
WITH leavers AS (
  SELECT count(*) AS n FROM employees
  WHERE termination_date >= '2025-01-01' AND termination_date < '2026-01-01'
),
headcount_start AS (
  SELECT count(*) AS n FROM employees
  WHERE hire_date < '2025-01-01'
    AND (termination_date IS NULL OR termination_date >= '2025-01-01')
)
SELECT l.n AS 离职人数, h.n AS 期初在职,
       round(l.n::numeric * 100 / h.n, 2) AS 离职率百分比
FROM leavers l, headcount_start h;

\echo '=== Q4 2025年各部门离职率 ==='
WITH leavers AS (
  SELECT dept_id, count(*) AS l FROM employees
  WHERE termination_date >= '2025-01-01' AND termination_date < '2026-01-01'
  GROUP BY dept_id
),
hc AS (
  SELECT dept_id, count(*) AS h FROM employees
  WHERE hire_date < '2025-01-01'
    AND (termination_date IS NULL OR termination_date >= '2025-01-01')
  GROUP BY dept_id
)
SELECT d.dept_name AS 部门, coalesce(l.l,0) AS 离职人数, h.h AS 期初在职,
       round(coalesce(l.l,0)::numeric * 100 / h.h, 2) AS 离职率百分比
FROM hc h
JOIN departments d ON d.dept_id = h.dept_id
LEFT JOIN leavers l ON l.dept_id = h.dept_id
ORDER BY 离职率百分比 DESC;

\echo '=== Q5 2026上半年月度人力成本 (应发合计) ==='
SELECT pay_period AS 月份,
       round(sum(base_pay + overtime_pay + bonus), 2) AS 人力成本应发,
       count(*) AS 发薪人次
FROM salary_payments
WHERE pay_period BETWEEN '2026-01' AND '2026-06'
GROUP BY pay_period ORDER BY pay_period;

\echo '=== Q6 各职级在职员工平均基本工资 ==='
SELECT job_level AS 职级, count(*) AS 人数,
       round(avg(base_salary), 0) AS 平均基本工资
FROM employees WHERE status = '在职'
GROUP BY job_level
ORDER BY avg(base_salary) DESC;

\echo '=== Q7 2026年新入职人数与来源分布 ==='
WITH newhires AS (
  SELECT emp_id FROM employees
  WHERE hire_date BETWEEN '2026-01-01' AND '2026-08-31'
)
SELECT count(*) AS 新入职总人数 FROM newhires;

SELECT c.source AS 来源, count(*) AS 人数
FROM candidates c
JOIN employees e ON c.hired_emp_id = e.emp_id
WHERE e.hire_date BETWEEN '2026-01-01' AND '2026-08-31'
GROUP BY c.source ORDER BY 人数 DESC;

\echo '=== Q8 2025年各类假期已批准总天数 ==='
SELECT leave_type AS 假期类型, sum(days) AS 总天数, count(*) AS 单数
FROM leave_requests
WHERE status = '已批准'
  AND start_date BETWEEN '2025-01-01' AND '2025-12-31'
GROUP BY leave_type ORDER BY 总天数 DESC;

\echo '=== Q9 2025年招聘漏斗: 投递→入职 ==='
WITH funnel AS (
  SELECT count(*) AS applied,
         count(*) FILTER (WHERE stage = '已入职') AS hired
  FROM candidates
  WHERE applied_at BETWEEN '2025-01-01' AND '2025-12-31'
)
SELECT applied AS 投递人数, hired AS 入职人数,
       round(hired::numeric * 100 / applied, 2) AS 转化率百分比
FROM funnel;

\echo '=== Q10 在职性别比例与平均工资 ==='
SELECT gender AS 性别, count(*) AS 人数,
       round(count(*)::numeric * 100 / sum(count(*)) OVER (), 2) AS 占比,
       round(avg(base_salary), 0) AS 平均基本工资
FROM employees WHERE status = '在职'
GROUP BY gender;

\echo '=== Q11 2026H1 人均月加班前3部门 ==='
WITH ot AS (
  SELECT d.dept_name, sum(a.overtime_hours) AS total_ot,
         count(DISTINCT a.emp_id) AS headcount
  FROM attendance_records a
  JOIN employees e ON a.emp_id = e.emp_id
  JOIN departments d ON e.dept_id = d.dept_id
  WHERE a.att_date BETWEEN '2026-01-01' AND '2026-06-30'
  GROUP BY d.dept_name
)
SELECT dept_name AS 部门, round(total_ot, 0) AS 加班总时长,
       round(total_ot / (headcount * 6.0), 2) AS 人均月加班小时
FROM ot ORDER BY 人均月加班小时 DESC LIMIT 3;

\echo '=== Q12 2026H1 绩效C/D 在职员工部门分布 Top ==='
SELECT d.dept_name AS 部门, count(*) AS 低绩效人数
FROM performance_reviews pr
JOIN employees e ON pr.emp_id = e.emp_id
JOIN departments d ON e.dept_id = d.dept_id
WHERE pr.review_period = '2026H1'
  AND pr.grade IN ('C', 'D')
  AND e.status = '在职'
GROUP BY d.dept_name ORDER BY 低绩效人数 DESC LIMIT 5;
