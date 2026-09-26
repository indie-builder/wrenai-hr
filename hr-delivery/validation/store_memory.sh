#!/usr/bin/env bash
# 把验证过的 NL→SQL 对写入 wren 语义记忆 (knowledge/sql/), 沉淀为可复用资产
# (v1 遗留脚本; 批量写入会修改语义记忆, 运行前确认)
set -uo pipefail
cd "$(dirname "$0")/../.."
ROOT="$PWD"
W="${WREN_BIN:-$ROOT/.venv/bin/wren}"
cd "$ROOT/hr-delivery/wren-project"
if [ ! -x "$W" ]; then
  echo "错误: 未找到 wren CLI: $W" >&2
  echo "请在仓库根目录执行: python3 -m venv .venv && .venv/bin/python -m pip install 'wrenai[memory]==0.13.4' (见 hr-delivery/README.md)" >&2
  exit 1
fi
if [ ! -f .env ]; then
  echo "错误: 缺少 hr-delivery/wren-project/.env (可为空文件)" >&2
  exit 1
fi
set -a
# 临时关闭 -u: .env 值可能引用外部变量, 不应因此崩溃
set +u
. ./.env
set -u
set +a

store() { $W memory store --nl "$1" --sql "$2" -q 2>&1 | tail -1; }

store "目前公司在职员工总人数是多少？" \
"SELECT count(*) AS 在职人数 FROM employees WHERE status = '在职'"

store "各部门在职人数分布，从高到低" \
"SELECT dept_name AS 部门, count(*) AS 在职人数 FROM v_active_employees GROUP BY dept_name ORDER BY 在职人数 DESC"

store "2025年公司整体离职率是多少？" \
"WITH leavers AS (SELECT count(*) AS n FROM employees WHERE termination_date >= DATE '2025-01-01' AND termination_date < DATE '2026-01-01'), hc AS (SELECT count(*) AS n FROM employees WHERE hire_date < DATE '2025-01-01' AND (termination_date IS NULL OR termination_date >= DATE '2025-01-01')) SELECT l.n AS 离职人数, h.n AS 期初在职, round(l.n * 100.0 / h.n, 2) AS 离职率百分比 FROM leavers l, hc h"

store "2026年上半年月度人力成本趋势（应发合计）" \
"SELECT pay_period AS 月份, round(sum(base_pay + overtime_pay + bonus), 2) AS 人力成本应发, count(*) AS 发薪人次 FROM v_monthly_salary WHERE pay_period BETWEEN '2026-01' AND '2026-06' GROUP BY pay_period ORDER BY pay_period"

store "各职级在职员工的平均基本工资" \
"SELECT job_level AS 职级, count(*) AS 人数, round(avg(base_salary), 0) AS 平均基本工资 FROM employees WHERE status = '在职' GROUP BY job_level ORDER BY avg(base_salary) DESC"

store "2026年新入职员工有多少人？按招聘来源分布" \
"SELECT c.source AS 来源, count(*) AS 人数 FROM candidates c JOIN employees e ON c.hired_emp_id = e.emp_id WHERE e.hire_date BETWEEN '2026-01-01' AND '2026-08-31' GROUP BY c.source ORDER BY 人数 DESC"

store "2025年各类假期已批准的总天数" \
"SELECT leave_type AS 假期类型, sum(days) AS 总天数, count(*) AS 单数 FROM leave_requests WHERE status = '已批准' AND start_date BETWEEN '2025-01-01' AND '2025-12-31' GROUP BY leave_type ORDER BY 总天数 DESC"

store "2025年招聘漏斗：投递人数、入职人数、投递到入职的转化率" \
"WITH funnel AS (SELECT count(*) AS applied, count(*) FILTER (WHERE stage = '已入职') AS hired FROM candidates WHERE applied_at BETWEEN '2025-01-01' AND '2025-12-31') SELECT applied AS 投递人数, hired AS 入职人数, round(hired * 100.0 / applied, 2) AS 转化率百分比 FROM funnel"

store "在职员工的男女比例和男女平均基本工资差异" \
"SELECT gender AS 性别, count(*) AS 人数, round(count(*) * 100.0 / sum(count(*)) OVER (), 2) AS 占比, round(avg(base_salary), 0) AS 平均基本工资 FROM employees WHERE status = '在职' GROUP BY gender"

store "2026年上半年人均月加班小时最多的前3个部门" \
"WITH ot AS (SELECT d.dept_name, sum(a.overtime_hours) AS total_ot, count(DISTINCT a.emp_id) AS headcount FROM attendance_records a JOIN employees e ON a.emp_id = e.emp_id JOIN departments d ON e.dept_id = d.dept_id WHERE a.att_date BETWEEN '2026-01-01' AND '2026-06-30' GROUP BY d.dept_name) SELECT dept_name AS 部门, round(total_ot, 0) AS 加班总时长, round(total_ot / (headcount * 6.0), 2) AS 人均月加班小时 FROM ot ORDER BY 人均月加班小时 DESC LIMIT 3"

store "目前在职员工中，2026H1绩效为C或D的人数最多的部门是哪个？" \
"SELECT d.dept_name AS 部门, count(*) AS 低绩效人数 FROM performance_reviews pr JOIN employees e ON pr.emp_id = e.emp_id JOIN departments d ON e.dept_id = d.dept_id WHERE pr.review_period = '2026H1' AND pr.grade IN ('C','D') AND e.status = '在职' GROUP BY d.dept_name ORDER BY 低绩效人数 DESC LIMIT 5"

echo "stored."
