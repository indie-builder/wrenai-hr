#!/usr/bin/env bash
# 装载 HR 数据: 建表 + \copy 种子数据 + SQL 生成考勤
# 前提: HR PostgreSQL 容器 wrenai-hr-pg 已启动 (hr/hr_demo, 宿主机端口 25432)
set -euo pipefail
cd "$(dirname "$0")"

PG="docker exec -i wrenai-hr-pg psql -U hr -d hr_demo -v ON_ERROR_STOP=1"
OUT=seed/out

echo "== 1/3 schema =="
$PG -q -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
$PG < schema.sql

copy_full() {  # CSV 含全部列(含显式主键)
  cat "$OUT/$1.csv" | docker exec -i wrenai-hr-pg psql -U hr -d hr_demo -q \
    -c "\copy $1 FROM STDIN WITH (FORMAT csv, HEADER true)"
  echo "  $1 OK"
}

copy_cols() {  # CSV 不含 serial 主键, 显式列清单
  local t=$1; shift
  cat "$OUT/$t.csv" | docker exec -i wrenai-hr-pg psql -U hr -d hr_demo -q \
    -c "\\copy $t$1 FROM STDIN WITH (FORMAT csv, HEADER true)"
  echo "  $t OK"
}

echo "== 2/3 种子数据 =="
copy_full departments
copy_full employees
copy_cols salary_payments   "(emp_id,pay_period,base_pay,overtime_pay,bonus,social_insurance,income_tax,net_pay,pay_date)"
copy_cols leave_requests    "(emp_id,leave_type,start_date,end_date,days,status,approver_id,applied_at)"
copy_cols job_openings      "(dept_id,job_title,job_level,headcount,salary_min,salary_max,status,opened_at,closed_at,hired_count)"
copy_cols candidates        "(name,gender,opening_id,source,stage,applied_at,expected_salary,hired_emp_id)"
copy_cols interviews        "(cand_id,round,interviewer_id,interview_date,score,result)"
copy_cols performance_reviews "(emp_id,review_period,score,grade,reviewer_id,comment)"
copy_cols training_records  "(emp_id,course_name,training_date,hours,completed,score)"
copy_cols transfers         "(emp_id,from_dept_id,to_dept_id,transfer_date,reason)"

echo "== 2b/3 v2 扩展数据 =="
OUT2=seed/out2
copy_cols2() {  # v2 扩展表: CSV 不含 serial 主键
  local t=$1; shift
  cat "$OUT2/$t.csv" | docker exec -i wrenai-hr-pg psql -U hr -d hr_demo -q \
    -c "\\copy $t$1 FROM STDIN WITH (FORMAT csv, HEADER true)"
  echo "  $t OK"
}
copy_cols2 headcount_plan      "(plan_year,dept_id,planned_headcount,budget_labor_cost,approved_at)"
copy_cols2 promotions          "(emp_id,promo_date,from_level,to_level,from_title,to_title,salary_before,salary_after,reason)"
copy_cols2 contracts           "(contract_no,emp_id,contract_type,start_date,end_date,renewals,status,signed_date)"
copy_cols2 salary_changes      "(emp_id,effective_date,salary_before,salary_after,change_pct,change_type)"
copy_cols2 insurance_payments  "(emp_id,pay_period,pension,medical,unemployment,injury,maternity,housing_fund,company_total)"
copy_cols2 awards_penalties    "(emp_id,record_date,record_type,category,amount,reason,approver_id)"
copy_cols2 overtime_requests   "(emp_id,ot_date,planned_hours,actual_hours,reason,status,compensation,applied_at)"
copy_cols2 leave_balances      "(emp_id,balance_type,as_of_quarter,entitled,used,remaining,expired)"
copy_cols2 offers              "(cand_id,offer_date,offer_salary,status,response_date,reject_reason)"
copy_cols2 recruitment_costs   "(channel,cost_month,amount,notes)"
copy_cols2 performance_goals   "(emp_id,review_period,goal_type,goal_desc,weight,completion_pct)"
copy_cols2 talent_pool         "(emp_id,pool_type,target_position,potential_rating,nominated_date,nominated_by,status)"
copy_cols2 engagement_surveys  "(emp_id,survey_year,engagement_score,recognition,growth,pay_satisfaction,manager_trust,work_life_balance)"
copy_cols2 exit_interviews     "(emp_id,interview_date,real_reason_category,satisfaction,would_recommend,comment)"

echo "== 3/3 考勤 (SQL 生成 2025-01 ~ 2026-08) =="
$PG < gen_attendance.sql

# 显式主键插入后同步序列
$PG -q -c "SELECT setval('employees_emp_id_seq', (SELECT max(emp_id) FROM employees));
           SELECT setval('departments_dept_id_seq', (SELECT max(dept_id) FROM departments));" >/dev/null

echo "== 行数统计 =="
$PG -c "SELECT 'departments' t, count(*) FROM departments UNION ALL
        SELECT 'employees', count(*) FROM employees UNION ALL
        SELECT 'salary_payments', count(*) FROM salary_payments UNION ALL
        SELECT 'attendance_records', count(*) FROM attendance_records UNION ALL
        SELECT 'leave_requests', count(*) FROM leave_requests UNION ALL
        SELECT 'job_openings', count(*) FROM job_openings UNION ALL
        SELECT 'candidates', count(*) FROM candidates UNION ALL
        SELECT 'interviews', count(*) FROM interviews UNION ALL
        SELECT 'performance_reviews', count(*) FROM performance_reviews UNION ALL
        SELECT 'training_records', count(*) FROM training_records UNION ALL
        SELECT 'transfers', count(*) FROM transfers UNION ALL
        SELECT 'headcount_plan', count(*) FROM headcount_plan UNION ALL
        SELECT 'promotions', count(*) FROM promotions UNION ALL
        SELECT 'contracts', count(*) FROM contracts UNION ALL
        SELECT 'salary_changes', count(*) FROM salary_changes UNION ALL
        SELECT 'insurance_payments', count(*) FROM insurance_payments UNION ALL
        SELECT 'awards_penalties', count(*) FROM awards_penalties UNION ALL
        SELECT 'overtime_requests', count(*) FROM overtime_requests UNION ALL
        SELECT 'leave_balances', count(*) FROM leave_balances UNION ALL
        SELECT 'offers', count(*) FROM offers UNION ALL
        SELECT 'recruitment_costs', count(*) FROM recruitment_costs UNION ALL
        SELECT 'performance_goals', count(*) FROM performance_goals UNION ALL
        SELECT 'talent_pool', count(*) FROM talent_pool UNION ALL
        SELECT 'engagement_surveys', count(*) FROM engagement_surveys UNION ALL
        SELECT 'exit_interviews', count(*) FROM exit_interviews ORDER BY 1;"
