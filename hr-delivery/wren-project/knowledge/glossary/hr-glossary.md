# HR 词汇表 (中英对照与同义词)

| 业务词 | 口径/映射 |
|---|---|
| HC / 编制 / 头数 / 人数 | 在职员工数: employees.status='在职' 的 count |
| 离职率 / 流失率 / turnover rate | 期间离职人数 ÷ 期初在职人数 (见 rules/general.md 官方口径) |
| 人力成本 / 人员成本 / 薪酬总额 | gross_pay = base_pay + overtime_pay + bonus (税前应发) |
| 实发 / 到手工资 | net_pay |
| 基本工资 / 底薪 | base_salary (当前快照) / base_pay (历史月度) |
| 月薪 | 默认指税前月薪 (元/月) |
| 司龄 / 工龄 | tenure_years (本公司司龄; 不含外部工龄) |
| 年终奖 | bonus 中每年 1 月发放的部分, 对应上一年度 |
| 招聘漏斗 | 投递→面试→offer→入职 (candidates.stage / interviews) |
| 转化率 / 转正率(招聘) | 阶段通过人数 ÷ 进入该阶段人数 |
| 出勤率 | (正常 + 远程办公) 天数 ÷ 应出勤工作日天数 |
| 加班 | attendance_records.overtime_hours |
| 低绩效 | grade IN ('C','D') |
| 绩效等级 | S > A > B > C > D |
| 金三银四/金九银十 | 招聘旺季: 3-4 月与 9-10 月 |
| 北上深杭蓉 | work_city 枚举: 北京/上海/深圳/杭州/成都/远程 |
| 全职/兼职/实习/外包 | employment_type 枚举 |
| 星辰科技 | 演示公司名, 数据均为虚构 |

## v2 扩展词汇

| 业务词 | 口径/映射 |
|---|---|
| 编制 / 编制达成率 | headcount_plan 计划编制; 达成率 = 年末实际在职 ÷ 计划编制 |
| 人力预算 / 成本预算 | headcount_plan.budget_labor_cost (年度, 税前应发口径) |
| 人力总成本 / 完全用人成本 | 税前应发 + 社保公积金企业缴纳 (insurance_payments.company_total) |
| 五险一金(企业) | insurance_payments: pension+medical+unemployment+injury+maternity+housing_fund |
| 调薪 / 涨薪 | salary_changes; 渗透率 = 调薪去重人数 ÷ 期初在职 |
| 晋升 / 升职 | promotions; 晋升率 = count(DISTINCT emp_id) ÷ 期初在职; 幅度在查询中按 (salary_after - salary_before) × 100.0 / salary_before 计算 |
| 合同续签 / 到期预警 | contracts; is_expiring_soon = 履行中且在2026-08-31至其后90天闭区间内到期，排除此前已到期 |
| 加班审批 / 加班单 | overtime_requests; 通过率 = 已批准 ÷ 全部 |
| 调休补偿 | overtime_requests.compensation='调休' (1天=8小时) |
| 假期余额 / 年假余额 | leave_balances 季度快照; 使用率 = used/entitled |
| Offer / 录用通知 | offers; 接受率 = 已接受 ÷ (已接受+已拒绝) |
| 招聘成本 / 渠道费用 | recruitment_costs; 单人成本 = 费用合计 ÷ 新入职人数 |
| KPI / OKR / 目标完成率 | performance_goals; 按权重加权平均 completion_pct |
| 高潜 / 继任者 / 人才池 | talent_pool; 继任覆盖率 = 有在池继任者的总监岗 ÷ 总监岗总数 |
| 敬业度 / eNPS调研 | engagement_surveys (1-5分, 年度); 低敬业度 < 3.0 |
| 真实离职原因 | exit_interviews.real_reason_category (面谈), 对比档案原因 termination_reason |
| 主动离职率 / 被动离职率 | 官方离职率口径 × is_voluntary 拆分 |
| 人效 | 人均人力成本类指标 (公司无营收数据, 用成本口径) |
| 转正 | 试用期结束转全职 (本数据集未单独建表, 实习转正不涉及) |
