-- ============================================================
-- 星辰科技 HR 演示数据库 (DuckDB)
-- 由 schema.sql 转换: SERIAL 主键改为 INTEGER (主键值由 build_duckdb.py 生成),
--   不声明 REFERENCES 外键 (DuckDB 1.5 会即时执行外键校验, 自引用装载会失败;
--   表间一致性由 CSV 源数据与装载顺序保证, 关系定义见 wren-project/relationships.yml)
-- 业务时间范围: 2020-06 (公司成立) ~ 2026-08-31 (数据截至)
-- 注意: 库文件必须命名为 public.duckdb —— wren 的 duckdb 连接器以文件名作为
--       挂载 catalog 别名, MDL 规划 SQL 以 "public" 前缀限定物理表
-- ============================================================

-- ============================================================
-- 星辰科技 HR 演示数据库 (PostgreSQL 16)
-- 业务时间范围: 2020-06 (公司成立) ~ 2026-08-31 (数据截至)
-- 薪资/考勤/绩效等记录自 2023-01 起完整保留
-- ============================================================

-- 部门表
CREATE TABLE departments (
  dept_id          INTEGER PRIMARY KEY,
  dept_name        VARCHAR(50)  NOT NULL UNIQUE,
  parent_id        INTEGER     ,
  location         VARCHAR(30)  NOT NULL,
  established_date DATE         NOT NULL
);
COMMENT ON TABLE  departments              IS '部门维度表: 公司组织架构中的各部门';
COMMENT ON COLUMN departments.dept_id      IS '部门ID(主键)';
COMMENT ON COLUMN departments.dept_name    IS '部门名称, 如: 技术部/产品部/人事部';
COMMENT ON COLUMN departments.parent_id    IS '上级部门ID, 顶级部门为空';
COMMENT ON COLUMN departments.location     IS '部门主要办公地';
COMMENT ON COLUMN departments.established_date IS '部门成立日期';

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
COMMENT ON TABLE  employees                   IS '员工主表: 全量在职与离职员工档案';
COMMENT ON COLUMN employees.emp_id            IS '员工ID(主键)';
COMMENT ON COLUMN employees.emp_no            IS '员工工号, 格式 EMP0001';
COMMENT ON COLUMN employees.name              IS '员工姓名';
COMMENT ON COLUMN employees.gender            IS '性别: 男/女';
COMMENT ON COLUMN employees.birth_date        IS '出生日期';
COMMENT ON COLUMN employees.hire_date         IS '入职日期';
COMMENT ON COLUMN employees.dept_id           IS '所属部门ID(当前部门)';
COMMENT ON COLUMN employees.job_title         IS '职位名称, 如: 软件工程师/产品经理';
COMMENT ON COLUMN employees.job_level         IS '职级: 初级/中级/高级/专家/总监/副总裁';
COMMENT ON COLUMN employees.employment_type   IS '用工类型: 全职/兼职/实习/外包';
COMMENT ON COLUMN employees.status            IS '在职状态: 在职/离职';
COMMENT ON COLUMN employees.base_salary       IS '当前月基本工资(元, 税前)';
COMMENT ON COLUMN employees.manager_id        IS '直属上级员工ID';
COMMENT ON COLUMN employees.work_city         IS '工作城市: 北京/上海/深圳/杭州/成都/远程';
COMMENT ON COLUMN employees.email             IS '企业邮箱';
COMMENT ON COLUMN employees.phone             IS '手机号';
COMMENT ON COLUMN employees.education         IS '最高学历: 大专/本科/硕士/博士';
COMMENT ON COLUMN employees.termination_date  IS '离职日期, 在职员工为空';
COMMENT ON COLUMN employees.termination_reason IS '离职原因: 个人原因/薪酬原因/职业发展等';
COMMENT ON COLUMN employees.is_voluntary      IS '是否主动离职: true主动/false被动(辞退等)';

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
COMMENT ON TABLE  salary_payments                IS '月度薪资发放事实表: 2023-01 起每月每人一条';
COMMENT ON COLUMN salary_payments.pay_id         IS '发放记录ID(主键)';
COMMENT ON COLUMN salary_payments.emp_id         IS '员工ID';
COMMENT ON COLUMN salary_payments.pay_period     IS '薪资月份, 格式 YYYY-MM';
COMMENT ON COLUMN salary_payments.base_pay       IS '基本工资(税前)';
COMMENT ON COLUMN salary_payments.overtime_pay   IS '加班费';
COMMENT ON COLUMN salary_payments.bonus          IS '奖金: 含年终奖(1月发放)与项目奖';
COMMENT ON COLUMN salary_payments.social_insurance IS '社保公积金个人扣款';
COMMENT ON COLUMN salary_payments.income_tax     IS '个人所得税';
COMMENT ON COLUMN salary_payments.net_pay        IS '实发工资(税后到手)';
COMMENT ON COLUMN salary_payments.pay_date       IS '发放日期(次月10日)';

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
COMMENT ON TABLE  attendance_records               IS '每日考勤事实表: 2025-01 起每个工作日每人一条';
COMMENT ON COLUMN attendance_records.att_id        IS '考勤记录ID(主键)';
COMMENT ON COLUMN attendance_records.emp_id        IS '员工ID';
COMMENT ON COLUMN attendance_records.att_date      IS '考勤日期';
COMMENT ON COLUMN attendance_records.status        IS '考勤状态: 正常/迟到/早退/旷工/请假/远程办公';
COMMENT ON COLUMN attendance_records.work_hours    IS '当日工作小时数';
COMMENT ON COLUMN attendance_records.overtime_hours IS '当日加班小时数';

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
COMMENT ON TABLE  leave_requests             IS '请假申请单: 员工提交的各类假期申请';
COMMENT ON COLUMN leave_requests.leave_id    IS '请假单ID(主键)';
COMMENT ON COLUMN leave_requests.emp_id      IS '请假员工ID';
COMMENT ON COLUMN leave_requests.leave_type  IS '假期类型: 年假/事假/病假/婚假/产假/陪产假/调休';
COMMENT ON COLUMN leave_requests.start_date  IS '假期开始日期';
COMMENT ON COLUMN leave_requests.end_date    IS '假期结束日期';
COMMENT ON COLUMN leave_requests.days        IS '请假天数(支持0.5)';
COMMENT ON COLUMN leave_requests.status      IS '审批状态: 已批准/待审批/已拒绝';
COMMENT ON COLUMN leave_requests.approver_id IS '审批人员工ID';
COMMENT ON COLUMN leave_requests.applied_at  IS '申请提交时间';

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
COMMENT ON TABLE  job_openings              IS '招聘岗位表: 各部门的用人需求(headcount)';
COMMENT ON COLUMN job_openings.opening_id   IS '岗位ID(主键)';
COMMENT ON COLUMN job_openings.dept_id      IS '用人部门ID';
COMMENT ON COLUMN job_openings.job_title    IS '招聘岗位名称';
COMMENT ON COLUMN job_openings.job_level    IS '招聘职级';
COMMENT ON COLUMN job_openings.headcount    IS '计划招聘人数';
COMMENT ON COLUMN job_openings.salary_min   IS '岗位薪资下限(月)';
COMMENT ON COLUMN job_openings.salary_max   IS '岗位薪资上限(月)';
COMMENT ON COLUMN job_openings.status       IS '岗位状态: 招聘中/已关闭/暂停';
COMMENT ON COLUMN job_openings.opened_at    IS '岗位开放日期';
COMMENT ON COLUMN job_openings.closed_at    IS '岗位关闭日期';
COMMENT ON COLUMN job_openings.hired_count  IS '该岗位实际入职人数';

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
COMMENT ON TABLE  candidates                  IS '候选人表: 应聘各岗位的候选人及其进展';
COMMENT ON COLUMN candidates.cand_id          IS '候选人ID(主键)';
COMMENT ON COLUMN candidates.name             IS '候选人姓名';
COMMENT ON COLUMN candidates.gender           IS '候选人性别';
COMMENT ON COLUMN candidates.opening_id       IS '应聘岗位ID';
COMMENT ON COLUMN candidates.source           IS '候选人来源: 内推/招聘网站/猎头/校园招聘';
COMMENT ON COLUMN candidates.stage            IS '当前阶段: 简历筛选/面试中/已发offer/已入职/已淘汰/拒绝offer';
COMMENT ON COLUMN candidates.applied_at       IS '投递日期';
COMMENT ON COLUMN candidates.expected_salary  IS '期望月薪';
COMMENT ON COLUMN candidates.hired_emp_id     IS '入职后关联的员工ID, 未入职为空';

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
COMMENT ON TABLE  interviews                 IS '面试记录表: 候选人各轮面试结果';
COMMENT ON COLUMN interviews.interview_id    IS '面试记录ID(主键)';
COMMENT ON COLUMN interviews.cand_id         IS '候选人ID';
COMMENT ON COLUMN interviews.round           IS '面试轮次: 一面/二面/三面/HR面';
COMMENT ON COLUMN interviews.interviewer_id  IS '面试官员工ID';
COMMENT ON COLUMN interviews.interview_date  IS '面试日期';
COMMENT ON COLUMN interviews.score           IS '面试评分(0-100)';
COMMENT ON COLUMN interviews.result          IS '面试结果: 通过/未通过/待定';

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
COMMENT ON TABLE  performance_reviews             IS '绩效考核事实表: 每半年一次(2023H1起)';
COMMENT ON COLUMN performance_reviews.review_id     IS '考核记录ID(主键)';
COMMENT ON COLUMN performance_reviews.emp_id        IS '被考核员工ID';
COMMENT ON COLUMN performance_reviews.review_period IS '考核周期, 格式如 2025H1/2025H2';
COMMENT ON COLUMN performance_reviews.score         IS '绩效得分(0-100)';
COMMENT ON COLUMN performance_reviews.grade         IS '绩效等级: S/A/B/C/D';
COMMENT ON COLUMN performance_reviews.reviewer_id   IS '考核人(直属上级)员工ID';
COMMENT ON COLUMN performance_reviews.comment       IS '考核评语';

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
COMMENT ON TABLE  training_records             IS '培训记录表: 员工参加的培训课程';
COMMENT ON COLUMN training_records.training_id  IS '培训记录ID(主键)';
COMMENT ON COLUMN training_records.emp_id       IS '参训员工ID';
COMMENT ON COLUMN training_records.course_name  IS '课程名称, 如: 新员工入职培训/管理力提升';
COMMENT ON COLUMN training_records.training_date IS '培训日期';
COMMENT ON COLUMN training_records.hours        IS '课程时长(小时)';
COMMENT ON COLUMN training_records.completed    IS '是否完成: true/false';
COMMENT ON COLUMN training_records.score        IS '结业得分(0-100)';

-- 人事调动
CREATE TABLE transfers (
  transfer_id  INTEGER PRIMARY KEY,
  emp_id       INTEGER NOT NULL,
  from_dept_id INTEGER,
  to_dept_id   INTEGER NOT NULL,
  transfer_date DATE   NOT NULL,
  reason       VARCHAR(20) NOT NULL
);
COMMENT ON TABLE  transfers                IS '人事调动记录: 跨部门调动';
COMMENT ON COLUMN transfers.transfer_id    IS '调动记录ID(主键)';
COMMENT ON COLUMN transfers.emp_id         IS '调动员工ID';
COMMENT ON COLUMN transfers.from_dept_id   IS '原部门ID';
COMMENT ON COLUMN transfers.to_dept_id     IS '新部门ID';
COMMENT ON COLUMN transfers.transfer_date  IS '调动生效日期';
COMMENT ON COLUMN transfers.reason         IS '调动原因: 组织调整/个人发展/晋升';

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
COMMENT ON TABLE  headcount_plan                 IS '年度部门编制规划: 计划编制人数与人力成本预算';
COMMENT ON COLUMN headcount_plan.plan_id         IS '编制计划ID(主键)';
COMMENT ON COLUMN headcount_plan.plan_year       IS '规划年度';
COMMENT ON COLUMN headcount_plan.dept_id         IS '部门ID';
COMMENT ON COLUMN headcount_plan.planned_headcount IS '计划编制人数(年末)';
COMMENT ON COLUMN headcount_plan.budget_labor_cost IS '年度人力成本预算(元, 税前应发口径)';
COMMENT ON COLUMN headcount_plan.approved_at     IS '预算批准日期(上年11-12月)';

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
COMMENT ON TABLE  promotions              IS '晋升记录: 员工职级/职位晋升及调薪';
COMMENT ON COLUMN promotions.promo_id     IS '晋升记录ID(主键)';
COMMENT ON COLUMN promotions.emp_id       IS '晋升员工ID';
COMMENT ON COLUMN promotions.promo_date   IS '晋升生效日期';
COMMENT ON COLUMN promotions.from_level   IS '晋升前职级';
COMMENT ON COLUMN promotions.to_level     IS '晋升后职级';
COMMENT ON COLUMN promotions.from_title   IS '晋升前职位';
COMMENT ON COLUMN promotions.to_title     IS '晋升后职位';
COMMENT ON COLUMN promotions.salary_before IS '晋升前月基本工资(元)';
COMMENT ON COLUMN promotions.salary_after IS '晋升后月基本工资(元)';
COMMENT ON COLUMN promotions.reason       IS '晋升原因: 年度晋升/破格晋升/继任就任';

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
COMMENT ON TABLE  contracts                 IS '劳动合同: 员工合同签订/续签/解除记录';
COMMENT ON COLUMN contracts.contract_id     IS '合同ID(主键)';
COMMENT ON COLUMN contracts.emp_id          IS '员工ID';
COMMENT ON COLUMN contracts.contract_no     IS '合同编号';
COMMENT ON COLUMN contracts.contract_type   IS '合同类型: 固定期限/无固定期限/实习协议/劳务协议';
COMMENT ON COLUMN contracts.start_date      IS '合同开始日期';
COMMENT ON COLUMN contracts.end_date        IS '合同结束日期, 无固定期限为空';
COMMENT ON COLUMN contracts.renewals        IS '已续签次数';
COMMENT ON COLUMN contracts.status          IS '合同状态: 履行中/已到期/已解除';
COMMENT ON COLUMN contracts.signed_date     IS '签订日期';

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
COMMENT ON TABLE  salary_changes                IS '调薪记录: 员工月基本工资调整历史';
COMMENT ON COLUMN salary_changes.change_id      IS '调薪记录ID(主键)';
COMMENT ON COLUMN salary_changes.emp_id         IS '调薪员工ID';
COMMENT ON COLUMN salary_changes.effective_date IS '生效日期';
COMMENT ON COLUMN salary_changes.salary_before  IS '调薪前月基本工资(元)';
COMMENT ON COLUMN salary_changes.salary_after   IS '调薪后月基本工资(元)';
COMMENT ON COLUMN salary_changes.change_pct     IS '调薪幅度(百分比, 如 5.20 表示 +5.2%)';
COMMENT ON COLUMN salary_changes.change_type    IS '调薪类型: 年度调薪/晋升调薪/特批调薪/转正调薪';

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
COMMENT ON TABLE  insurance_payments                IS '社保公积金企业缴纳月度记录(与薪资同期)';
COMMENT ON COLUMN insurance_payments.ins_id        IS '缴纳记录ID(主键)';
COMMENT ON COLUMN insurance_payments.emp_id        IS '员工ID';
COMMENT ON COLUMN insurance_payments.pay_period    IS '缴纳月份, 格式 YYYY-MM';
COMMENT ON COLUMN insurance_payments.pension       IS '养老保险企业缴纳(元)';
COMMENT ON COLUMN insurance_payments.medical       IS '医疗保险企业缴纳(元)';
COMMENT ON COLUMN insurance_payments.unemployment  IS '失业保险企业缴纳(元)';
COMMENT ON COLUMN insurance_payments.injury        IS '工伤保险企业缴纳(元)';
COMMENT ON COLUMN insurance_payments.maternity     IS '生育保险企业缴纳(元)';
COMMENT ON COLUMN insurance_payments.housing_fund  IS '住房公积金企业缴纳(元)';
COMMENT ON COLUMN insurance_payments.company_total IS '企业缴纳合计(元)';

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
COMMENT ON TABLE  awards_penalties              IS '奖惩记录: 员工奖励与处罚';
COMMENT ON COLUMN awards_penalties.record_id    IS '记录ID(主键)';
COMMENT ON COLUMN awards_penalties.emp_id       IS '员工ID';
COMMENT ON COLUMN awards_penalties.record_date  IS '记录日期';
COMMENT ON COLUMN awards_penalties.record_type  IS '类型: 奖励/处罚';
COMMENT ON COLUMN awards_penalties.category     IS '类别: 奖金/通报表扬/优秀员工/警告/记过/罚款';
COMMENT ON COLUMN awards_penalties.amount       IS '金额(元), 通报类为空';
COMMENT ON COLUMN awards_penalties.reason       IS '事由';
COMMENT ON COLUMN awards_penalties.approver_id  IS '审批人员工ID';

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
COMMENT ON TABLE  overtime_requests                IS '加班申请与审批';
COMMENT ON COLUMN overtime_requests.ot_id         IS '加班单ID(主键)';
COMMENT ON COLUMN overtime_requests.emp_id        IS '员工ID';
COMMENT ON COLUMN overtime_requests.ot_date       IS '加班日期';
COMMENT ON COLUMN overtime_requests.planned_hours IS '申请加班时长(小时)';
COMMENT ON COLUMN overtime_requests.actual_hours  IS '实际加班时长(小时), 未完成为空';
COMMENT ON COLUMN overtime_requests.reason        IS '加班事由';
COMMENT ON COLUMN overtime_requests.status        IS '审批状态: 已批准/已拒绝/待审批';
COMMENT ON COLUMN overtime_requests.compensation  IS '补偿方式: 调休/加班费/无';
COMMENT ON COLUMN overtime_requests.applied_at    IS '申请日期(加班前)';

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
COMMENT ON TABLE  leave_balances                IS '假期余额季度快照: 年假与调休';
COMMENT ON COLUMN leave_balances.balance_id     IS '余额记录ID(主键)';
COMMENT ON COLUMN leave_balances.emp_id         IS '员工ID';
COMMENT ON COLUMN leave_balances.balance_type   IS '假期类型: 年假/调休';
COMMENT ON COLUMN leave_balances.as_of_quarter  IS '快照季度, 格式如 2025Q3';
COMMENT ON COLUMN leave_balances.entitled       IS '本期总额度(天)';
COMMENT ON COLUMN leave_balances.used           IS '已使用(天, 截至季度末)';
COMMENT ON COLUMN leave_balances.remaining      IS '剩余(天)';
COMMENT ON COLUMN leave_balances.expired        IS '本期已失效(天, 年度清零)';

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
COMMENT ON TABLE  offers                   IS 'Offer发放记录';
COMMENT ON COLUMN offers.offer_id         IS 'Offer ID(主键)';
COMMENT ON COLUMN offers.cand_id          IS '候选人ID';
COMMENT ON COLUMN offers.offer_date       IS '发放日期';
COMMENT ON COLUMN offers.offer_salary     IS 'Offer 月薪(元)';
COMMENT ON COLUMN offers.status           IS '状态: 待回复/已接受/已拒绝';
COMMENT ON COLUMN offers.response_date    IS '回复日期';
COMMENT ON COLUMN offers.reject_reason    IS '拒绝原因: 薪酬不匹配/已接其他offer/家庭原因/其他';

-- 招聘渠道费用
CREATE TABLE recruitment_costs (
  cost_id    INTEGER PRIMARY KEY,
  channel    VARCHAR(10)  NOT NULL,
  cost_month CHAR(7)      NOT NULL,
  amount     NUMERIC(12,2) NOT NULL,
  notes      VARCHAR(100)
);
COMMENT ON TABLE  recruitment_costs             IS '招聘渠道月度费用';
COMMENT ON COLUMN recruitment_costs.cost_id     IS '费用记录ID(主键)';
COMMENT ON COLUMN recruitment_costs.channel     IS '渠道: 招聘网站/猎头/内推奖励/校园招聘';
COMMENT ON COLUMN recruitment_costs.cost_month  IS '费用月份, 格式 YYYY-MM';
COMMENT ON COLUMN recruitment_costs.amount      IS '费用金额(元)';
COMMENT ON COLUMN recruitment_costs.notes       IS '备注';

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
COMMENT ON TABLE  performance_goals                  IS '绩效目标: 员工半年度 KPI/OKR 及完成率';
COMMENT ON COLUMN performance_goals.goal_id         IS '目标ID(主键)';
COMMENT ON COLUMN performance_goals.emp_id          IS '员工ID';
COMMENT ON COLUMN performance_goals.review_period   IS '考核周期, 格式如 2025H1';
COMMENT ON COLUMN performance_goals.goal_type       IS '目标类型: KPI/OKR';
COMMENT ON COLUMN performance_goals.goal_desc       IS '目标描述';
COMMENT ON COLUMN performance_goals.weight          IS '权重(%)';
COMMENT ON COLUMN performance_goals.completion_pct  IS '完成率(%)';

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
COMMENT ON TABLE  talent_pool                    IS '人才池: 高潜人才与关键岗位继任者';
COMMENT ON COLUMN talent_pool.pool_id            IS '记录ID(主键)';
COMMENT ON COLUMN talent_pool.emp_id             IS '员工ID';
COMMENT ON COLUMN talent_pool.pool_type          IS '类型: 高潜人才/继任者';
COMMENT ON COLUMN talent_pool.target_position    IS '继任目标岗位, 继任者必填';
COMMENT ON COLUMN talent_pool.potential_rating   IS '潜力评级: 高潜/潜力之星';
COMMENT ON COLUMN talent_pool.nominated_date     IS '提名日期';
COMMENT ON COLUMN talent_pool.nominated_by       IS '提名人员工ID';
COMMENT ON COLUMN talent_pool.status             IS '状态: 在池/已晋升/已移出';

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
COMMENT ON TABLE  engagement_surveys                  IS '敬业度调研: 年度全员调研(1-5分)';
COMMENT ON COLUMN engagement_surveys.survey_id        IS '调研记录ID(主键)';
COMMENT ON COLUMN engagement_surveys.emp_id           IS '员工ID';
COMMENT ON COLUMN engagement_surveys.survey_year      IS '调研年度';
COMMENT ON COLUMN engagement_surveys.engagement_score IS '敬业度总分(1-5)';
COMMENT ON COLUMN engagement_surveys.recognition      IS '认可与回报维度(1-5)';
COMMENT ON COLUMN engagement_surveys.growth           IS '成长与发展维度(1-5)';
COMMENT ON COLUMN engagement_surveys.pay_satisfaction IS '薪酬满意度维度(1-5)';
COMMENT ON COLUMN engagement_surveys.manager_trust    IS '管理者信任维度(1-5)';
COMMENT ON COLUMN engagement_surveys.work_life_balance IS '工作生活平衡维度(1-5)';

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
COMMENT ON TABLE  exit_interviews                    IS '离职面谈: 离职员工真实原因调研';
COMMENT ON COLUMN exit_interviews.exit_id             IS '面谈记录ID(主键)';
COMMENT ON COLUMN exit_interviews.emp_id              IS '离职员工ID';
COMMENT ON COLUMN exit_interviews.interview_date      IS '面谈日期(离职前一周内)';
COMMENT ON COLUMN exit_interviews.real_reason_category IS '真实原因归类: 薪酬福利/职业发展/管理问题/工作文化/家庭个人/健康';
COMMENT ON COLUMN exit_interviews.satisfaction        IS '公司满意度(1-5)';
COMMENT ON COLUMN exit_interviews.would_recommend     IS '是否愿意推荐他人入职';
COMMENT ON COLUMN exit_interviews.comment             IS '面谈备注';
