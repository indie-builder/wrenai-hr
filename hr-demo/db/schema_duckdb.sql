-- 星辰科技 HR 演示数据库，快照 2026-08-31。
-- 物理类型与约束由本文件定义；业务字段说明见 wren-project/models/。
-- INTEGER 主键由装载器生成；关系由 CSV 一致性及 relationships.yml 维护。
-- 库名 public.duckdb 决定 Wren 挂载的 public catalog。

-- 部门表
CREATE TABLE departments (
  dept_id          INTEGER PRIMARY KEY,
  dept_name        VARCHAR(50)  NOT NULL UNIQUE,
  parent_id        INTEGER     ,
  location         VARCHAR(30)  NOT NULL,
  established_date DATE         NOT NULL
);

-- 员工主表
CREATE TABLE employees (
  emp_id             INTEGER PRIMARY KEY,
  emp_no             VARCHAR(12)  NOT NULL UNIQUE,
  name               VARCHAR(30)  NOT NULL,
  gender             VARCHAR(4)   NOT NULL CHECK (gender IN ('男','女')),
  birth_date         DATE         NOT NULL,
  hire_date          DATE         NOT NULL,
  dept_id            INTEGER      NOT NULL,
  job_title          VARCHAR(50)  NOT NULL,
  job_level          VARCHAR(10)  NOT NULL CHECK (job_level IN ('初级','中级','高级','专家','总监','副总裁')),
  employment_type    VARCHAR(10)  NOT NULL CHECK (employment_type IN ('全职','兼职','实习','外包')),
  status             VARCHAR(10)  NOT NULL CHECK (status IN ('在职','离职')),
  base_salary        NUMERIC(10,2) NOT NULL,
  manager_id         INTEGER     ,
  work_city          VARCHAR(20)  NOT NULL,
  email              VARCHAR(80)  NOT NULL UNIQUE,
  phone              VARCHAR(20),
  education          VARCHAR(10)  NOT NULL CHECK (education IN ('大专','本科','硕士','博士')),
  termination_date   DATE,
  termination_reason VARCHAR(20),
  is_voluntary       BOOLEAN
);

-- 月度薪资发放记录
CREATE TABLE salary_payments (
  pay_id           INTEGER PRIMARY KEY,
  emp_id           INTEGER      NOT NULL,
  pay_period       CHAR(7)      NOT NULL,
  base_pay         NUMERIC(12,2) NOT NULL,
  overtime_pay     NUMERIC(12,2) NOT NULL DEFAULT 0,
  bonus            NUMERIC(12,2) NOT NULL DEFAULT 0,
  social_insurance NUMERIC(12,2) NOT NULL DEFAULT 0,
  income_tax       NUMERIC(12,2) NOT NULL DEFAULT 0,
  net_pay          NUMERIC(12,2) NOT NULL,
  pay_date         DATE         NOT NULL,
  UNIQUE (emp_id, pay_period)
);

-- 每日考勤记录
CREATE TABLE attendance_records (
  att_id         INTEGER PRIMARY KEY,
  emp_id         INTEGER    NOT NULL,
  att_date       DATE       NOT NULL,
  status         VARCHAR(10) NOT NULL CHECK (status IN ('正常','迟到','早退','旷工','请假','远程办公')),
  work_hours     NUMERIC(4,1) NOT NULL DEFAULT 8.0,
  overtime_hours NUMERIC(4,1) NOT NULL DEFAULT 0,
  UNIQUE (emp_id, att_date)
);

-- 请假申请单
CREATE TABLE leave_requests (
  leave_id    INTEGER PRIMARY KEY,
  emp_id      INTEGER    NOT NULL,
  leave_type  VARCHAR(10) NOT NULL CHECK (leave_type IN ('年假','事假','病假','婚假','产假','陪产假','调休')),
  start_date  DATE       NOT NULL,
  end_date    DATE       NOT NULL,
  days        NUMERIC(4,1) NOT NULL,
  status      VARCHAR(10) NOT NULL CHECK (status IN ('已批准','待审批','已拒绝')),
  approver_id INTEGER   ,
  applied_at  TIMESTAMP  NOT NULL
);

-- 招聘岗位 (headcount 需求)
CREATE TABLE job_openings (
  opening_id  INTEGER PRIMARY KEY,
  dept_id     INTEGER     NOT NULL,
  job_title   VARCHAR(50) NOT NULL,
  job_level   VARCHAR(10) NOT NULL,
  headcount   INTEGER     NOT NULL,
  salary_min  NUMERIC(10,2),
  salary_max  NUMERIC(10,2),
  status      VARCHAR(10) NOT NULL CHECK (status IN ('招聘中','已关闭','暂停')),
  opened_at   DATE        NOT NULL,
  closed_at   DATE,
  hired_count INTEGER     NOT NULL DEFAULT 0
);

-- 候选人
CREATE TABLE candidates (
  cand_id         INTEGER PRIMARY KEY,
  name            VARCHAR(30) NOT NULL,
  gender          VARCHAR(4)  NOT NULL CHECK (gender IN ('男','女')),
  opening_id      INTEGER     NOT NULL,
  source          VARCHAR(10) NOT NULL CHECK (source IN ('内推','招聘网站','猎头','校园招聘')),
  stage           VARCHAR(10) NOT NULL CHECK (stage IN ('简历筛选','面试中','已发offer','已入职','已淘汰','拒绝offer')),
  applied_at      DATE        NOT NULL,
  expected_salary NUMERIC(10,2),
  hired_emp_id    INTEGER
);

-- 面试记录
CREATE TABLE interviews (
  interview_id   INTEGER PRIMARY KEY,
  cand_id        INTEGER    NOT NULL,
  round          VARCHAR(6) NOT NULL CHECK (round IN ('一面','二面','三面','HR面')),
  interviewer_id INTEGER   ,
  interview_date DATE       NOT NULL,
  score          NUMERIC(3,1),
  result         VARCHAR(6) NOT NULL CHECK (result IN ('通过','未通过','待定'))
);

-- 绩效考核
CREATE TABLE performance_reviews (
  review_id     INTEGER PRIMARY KEY,
  emp_id        INTEGER     NOT NULL,
  review_period VARCHAR(8)  NOT NULL,
  score         NUMERIC(4,1) NOT NULL,
  grade         VARCHAR(2)  NOT NULL CHECK (grade IN ('S','A','B','C','D')),
  reviewer_id   INTEGER    ,
  comment       TEXT,
  UNIQUE (emp_id, review_period)
);

-- 培训记录
CREATE TABLE training_records (
  training_id  INTEGER PRIMARY KEY,
  emp_id       INTEGER     NOT NULL,
  course_name  VARCHAR(60) NOT NULL,
  training_date DATE       NOT NULL,
  hours        NUMERIC(4,1) NOT NULL,
  completed    BOOLEAN     NOT NULL,
  score        NUMERIC(4,1)
);

-- 人事调动
CREATE TABLE transfers (
  transfer_id  INTEGER PRIMARY KEY,
  emp_id       INTEGER NOT NULL,
  from_dept_id INTEGER,
  to_dept_id   INTEGER NOT NULL,
  transfer_date DATE   NOT NULL,
  reason       VARCHAR(20) NOT NULL
);

-- ============================================================
-- v2 扩展: HR 八大业务域补齐 (GOAL.md M1)
-- ============================================================

-- 编制规划
CREATE TABLE headcount_plan (
  plan_id           INTEGER PRIMARY KEY,
  plan_year         INTEGER      NOT NULL,
  dept_id           INTEGER      NOT NULL,
  planned_headcount INTEGER      NOT NULL,
  budget_labor_cost NUMERIC(14,2) NOT NULL,
  approved_at       DATE         NOT NULL,
  UNIQUE (plan_year, dept_id)
);

-- 晋升记录
CREATE TABLE promotions (
  promo_id      INTEGER PRIMARY KEY,
  emp_id        INTEGER      NOT NULL,
  promo_date    DATE         NOT NULL,
  from_level    VARCHAR(10)  NOT NULL,
  to_level      VARCHAR(10)  NOT NULL,
  from_title    VARCHAR(50)  NOT NULL,
  to_title      VARCHAR(50)  NOT NULL,
  salary_before NUMERIC(10,2) NOT NULL,
  salary_after  NUMERIC(10,2) NOT NULL,
  reason        VARCHAR(20)  NOT NULL
);

-- 劳动合同
CREATE TABLE contracts (
  contract_id INTEGER PRIMARY KEY,
  emp_id      INTEGER      NOT NULL,
  contract_no VARCHAR(20)  NOT NULL UNIQUE,
  contract_type VARCHAR(20) NOT NULL CHECK (contract_type IN ('固定期限','无固定期限','实习协议','劳务协议')),
  start_date  DATE         NOT NULL,
  end_date    DATE,
  renewals    INTEGER      NOT NULL DEFAULT 0,
  status      VARCHAR(10)  NOT NULL CHECK (status IN ('履行中','已到期','已解除')),
  signed_date DATE         NOT NULL
);

-- 调薪记录
CREATE TABLE salary_changes (
  change_id     INTEGER PRIMARY KEY,
  emp_id        INTEGER      NOT NULL,
  effective_date DATE        NOT NULL,
  salary_before NUMERIC(10,2) NOT NULL,
  salary_after  NUMERIC(10,2) NOT NULL,
  change_pct    NUMERIC(5,2)  NOT NULL,
  change_type   VARCHAR(10)  NOT NULL CHECK (change_type IN ('年度调薪','晋升调薪','特批调薪','转正调薪'))
);

-- 社保公积金企业缴纳
CREATE TABLE insurance_payments (
  ins_id       INTEGER PRIMARY KEY,
  emp_id       INTEGER      NOT NULL,
  pay_period   CHAR(7)      NOT NULL,
  pension      NUMERIC(10,2) NOT NULL,
  medical      NUMERIC(10,2) NOT NULL,
  unemployment NUMERIC(10,2) NOT NULL,
  injury       NUMERIC(10,2) NOT NULL,
  maternity    NUMERIC(10,2) NOT NULL,
  housing_fund NUMERIC(10,2) NOT NULL,
  company_total NUMERIC(10,2) NOT NULL,
  UNIQUE (emp_id, pay_period)
);

-- 奖惩记录
CREATE TABLE awards_penalties (
  record_id  INTEGER PRIMARY KEY,
  emp_id     INTEGER      NOT NULL,
  record_date DATE        NOT NULL,
  record_type VARCHAR(10) NOT NULL CHECK (record_type IN ('奖励','处罚')),
  category   VARCHAR(20)  NOT NULL,
  amount     NUMERIC(10,2),
  reason     VARCHAR(100) NOT NULL,
  approver_id INTEGER
);

-- 加班申请
CREATE TABLE overtime_requests (
  ot_id        INTEGER PRIMARY KEY,
  emp_id       INTEGER      NOT NULL,
  ot_date      DATE         NOT NULL,
  planned_hours NUMERIC(4,1) NOT NULL,
  actual_hours NUMERIC(4,1),
  reason       VARCHAR(100) NOT NULL,
  status       VARCHAR(10)  NOT NULL CHECK (status IN ('已批准','已拒绝','待审批')),
  compensation VARCHAR(10)  CHECK (compensation IN ('调休','加班费','无')),
  applied_at   DATE         NOT NULL
);

-- 假期余额
CREATE TABLE leave_balances (
  balance_id  INTEGER PRIMARY KEY,
  emp_id      INTEGER      NOT NULL,
  balance_type VARCHAR(10) NOT NULL CHECK (balance_type IN ('年假','调休')),
  as_of_quarter CHAR(6)    NOT NULL,
  entitled    NUMERIC(5,1) NOT NULL,
  used        NUMERIC(5,1) NOT NULL,
  remaining   NUMERIC(5,1) NOT NULL,
  expired     NUMERIC(5,1) NOT NULL DEFAULT 0,
  UNIQUE (emp_id, balance_type, as_of_quarter)
);

-- Offer 记录
CREATE TABLE offers (
  offer_id     INTEGER PRIMARY KEY,
  cand_id      INTEGER      NOT NULL,
  offer_date   DATE         NOT NULL,
  offer_salary NUMERIC(10,2) NOT NULL,
  status       VARCHAR(10)  NOT NULL CHECK (status IN ('待回复','已接受','已拒绝')),
  response_date DATE,
  reject_reason VARCHAR(20)
);

-- 招聘渠道费用
CREATE TABLE recruitment_costs (
  cost_id    INTEGER PRIMARY KEY,
  channel    VARCHAR(10)  NOT NULL,
  cost_month CHAR(7)      NOT NULL,
  amount     NUMERIC(12,2) NOT NULL,
  notes      VARCHAR(100)
);

-- 绩效目标
CREATE TABLE performance_goals (
  goal_id       INTEGER PRIMARY KEY,
  emp_id        INTEGER      NOT NULL,
  review_period VARCHAR(8)   NOT NULL,
  goal_type     VARCHAR(6)   NOT NULL CHECK (goal_type IN ('KPI','OKR')),
  goal_desc     VARCHAR(120) NOT NULL,
  weight        INTEGER      NOT NULL CHECK (weight BETWEEN 10 AND 100),
  completion_pct NUMERIC(5,1) NOT NULL,
  UNIQUE (emp_id, review_period, goal_desc)
);

-- 人才池
CREATE TABLE talent_pool (
  pool_id        INTEGER PRIMARY KEY,
  emp_id         INTEGER      NOT NULL,
  pool_type      VARCHAR(10)  NOT NULL CHECK (pool_type IN ('高潜人才','继任者')),
  target_position VARCHAR(50),
  potential_rating VARCHAR(10) NOT NULL,
  nominated_date DATE         NOT NULL,
  nominated_by   INTEGER     ,
  status         VARCHAR(10)  NOT NULL CHECK (status IN ('在池','已晋升','已移出'))
);

-- 敬业度调研
CREATE TABLE engagement_surveys (
  survey_id   INTEGER PRIMARY KEY,
  emp_id      INTEGER      NOT NULL,
  survey_year INTEGER      NOT NULL,
  engagement_score NUMERIC(3,1) NOT NULL,
  recognition NUMERIC(3,1) NOT NULL,
  growth      NUMERIC(3,1) NOT NULL,
  pay_satisfaction NUMERIC(3,1) NOT NULL,
  manager_trust NUMERIC(3,1) NOT NULL,
  work_life_balance NUMERIC(3,1) NOT NULL,
  UNIQUE (emp_id, survey_year)
);

-- 离职面谈
CREATE TABLE exit_interviews (
  exit_id       INTEGER PRIMARY KEY,
  emp_id        INTEGER      NOT NULL,
  interview_date DATE        NOT NULL,
  real_reason_category VARCHAR(20) NOT NULL,
  satisfaction  NUMERIC(3,1) NOT NULL,
  would_recommend BOOLEAN    NOT NULL,
  comment       VARCHAR(200)
);
