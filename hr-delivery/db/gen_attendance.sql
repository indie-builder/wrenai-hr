-- 考勤记录批量生成: 2025-01-01 ~ 2026-08-31 每个工作日 x 在职员工
-- 状态分布约: 正常 88% / 迟到 4% / 远程办公 3% / 请假 2% / 早退 1.5% / 旷工 1%
-- 技术部/运营部/客服部 加班概率更高; 周五加班更少
TRUNCATE attendance_records;

INSERT INTO attendance_records (emp_id, att_date, status, work_hours, overtime_hours)
SELECT
  emp_id,
  d,
  status,
  (CASE status
     WHEN '正常'     THEN 8.0
     WHEN '远程办公' THEN 8.0
     WHEN '迟到'     THEN round((7.0 + random() * 0.8)::numeric, 1)
     WHEN '早退'     THEN round((6.0 + random())::numeric, 1)
     ELSE 0.0 END)::numeric(4,1) AS work_hours,
  (CASE
     WHEN status IN ('请假', '旷工') THEN 0.0
     WHEN random() < (CASE WHEN dept_name IN ('技术部', '运营部', '客服部') THEN 0.35
                           WHEN dow = 5 THEN 0.08
                           ELSE 0.15 END)
       THEN (ARRAY[1.0,1.5,2.0,2.5,3.0,4.0])[1 + floor(random() * 6)::int]
     ELSE 0.0 END)::numeric(4,1) AS overtime_hours
FROM (
  SELECT e.emp_id,
         dp.dept_name,
         gs::date AS d,
         extract(isodow from gs)::int AS dow,
         CASE
           WHEN gs::date < e.hire_date THEN NULL
           WHEN e.termination_date IS NOT NULL AND gs::date > e.termination_date THEN NULL
           WHEN random() < 0.88 THEN '正常'
           WHEN random() < 0.35 THEN '迟到'
           WHEN random() < 0.40 THEN '远程办公'
           WHEN random() < 0.45 THEN '请假'
           WHEN random() < 0.60 THEN '早退'
           ELSE '旷工'
         END::varchar(10) AS status
  FROM employees e
  JOIN departments dp ON dp.dept_id = e.dept_id
  CROSS JOIN generate_series(DATE '2025-01-01', DATE '2026-08-31', INTERVAL '1 day') gs
  WHERE e.hire_date < DATE '2026-08-31'
    AND extract(isodow from gs) < 6
) x
WHERE status IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_att_emp  ON attendance_records(emp_id);
CREATE INDEX IF NOT EXISTS idx_att_date ON attendance_records(att_date);
ANALYZE attendance_records;
