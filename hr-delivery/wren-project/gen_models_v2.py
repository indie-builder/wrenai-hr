#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 v2 扩展的 14 个模型 YAML (中文描述, 与 schema.sql 同步)"""
import pathlib

MODELS = {
"headcount_plan": {
  "desc": "年度部门编制规划: 计划编制人数与人力成本预算",
  "pk": "plan_id",
  "ref": {"schema": "public", "table": "headcount_plan"},
  "cols": [
    ("plan_id","INTEGER","编制计划ID(主键)",{"not_null": True}),
    ("plan_year","INTEGER","规划年度",{}),
    ("dept_id","INTEGER","部门ID",{}),
    ("planned_headcount","INTEGER","计划编制人数(年末)",{}),
    ("budget_labor_cost","DECIMAL(14,2)","年度人力成本预算(元, 税前应发口径)",{}),
    ("approved_at","DATE","预算批准日期(上年11-12月)",{}),
  ]},
"promotions": {
  "desc": "晋升记录: 员工职级/职位晋升及调薪",
  "pk": "promo_id",
  "ref": {"schema": "public", "table": "promotions"},
  "cols": [
    ("promo_id","INTEGER","晋升记录ID(主键)",{"not_null": True}),
    ("emp_id","INTEGER","晋升员工ID",{}),
    ("promo_date","DATE","晋升生效日期",{}),
    ("from_level","VARCHAR","晋升前职级",{}),
    ("to_level","VARCHAR","晋升后职级",{}),
    ("from_title","VARCHAR","晋升前职位",{}),
    ("to_title","VARCHAR","晋升后职位",{}),
    ("salary_before","DECIMAL(10,2)","晋升前月基本工资(元)",{}),
    ("salary_after","DECIMAL(10,2)","晋升后月基本工资(元)",{}),
    ("reason","VARCHAR","晋升原因: 年度晋升/破格晋升/继任就任",{}),
    ("raise_pct","DECIMAL(5,2)","晋升调薪幅度(%)",{"calc": "round((salary_after - salary_before) * 100.0 / salary_before, 2)"}),
  ]},
"contracts": {
  "desc": "劳动合同: 员工合同签订/续签/解除记录",
  "pk": "contract_id",
  "ref": {"schema": "public", "table": "contracts"},
  "cols": [
    ("contract_id","INTEGER","合同ID(主键)",{"not_null": True}),
    ("emp_id","INTEGER","员工ID",{}),
    ("contract_no","VARCHAR","合同编号",{}),
    ("contract_type","VARCHAR","合同类型: 固定期限/无固定期限/实习协议/劳务协议",{}),
    ("start_date","DATE","合同开始日期",{}),
    ("end_date","DATE","合同结束日期, 无固定期限为空",{}),
    ("renewals","INTEGER","已续签次数",{}),
    ("status","VARCHAR","合同状态: 履行中/已到期/已解除",{}),
    ("signed_date","DATE","签订日期",{}),
    ("is_expiring_soon","BOOLEAN","是否90天内到期(履行中)",{"calc": "CASE WHEN status = '履行中' AND end_date IS NOT NULL AND end_date <= current_date + INTERVAL '90 days' THEN true ELSE false END"}),
  ]},
"salary_changes": {
  "desc": "调薪记录: 员工月基本工资调整历史",
  "pk": "change_id",
  "ref": {"schema": "public", "table": "salary_changes"},
  "cols": [
    ("change_id","INTEGER","调薪记录ID(主键)",{"not_null": True}),
    ("emp_id","INTEGER","调薪员工ID",{}),
    ("effective_date","DATE","生效日期",{}),
    ("salary_before","DECIMAL(10,2)","调薪前月基本工资(元)",{}),
    ("salary_after","DECIMAL(10,2)","调薪后月基本工资(元)",{}),
    ("change_pct","DECIMAL(5,2)","调薪幅度(百分比)",{}),
    ("change_type","VARCHAR","调薪类型: 年度调薪/晋升调薪/特批调薪/转正调薪",{}),
  ]},
"insurance_payments": {
  "desc": "社保公积金企业缴纳月度记录(与薪资同期, 缴费基数=当月基本工资)",
  "pk": "ins_id",
  "ref": {"schema": "public", "table": "insurance_payments"},
  "cols": [
    ("ins_id","INTEGER","缴纳记录ID(主键)",{"not_null": True}),
    ("emp_id","INTEGER","员工ID",{}),
    ("pay_period","VARCHAR","缴纳月份, 格式 YYYY-MM",{}),
    ("pension","DECIMAL(10,2)","养老保险企业缴纳(元)",{}),
    ("medical","DECIMAL(10,2)","医疗保险企业缴纳(元)",{}),
    ("unemployment","DECIMAL(10,2)","失业保险企业缴纳(元)",{}),
    ("injury","DECIMAL(10,2)","工伤保险企业缴纳(元)",{}),
    ("maternity","DECIMAL(10,2)","生育保险企业缴纳(元)",{}),
    ("housing_fund","DECIMAL(10,2)","住房公积金企业缴纳(元)",{}),
    ("company_total","DECIMAL(10,2)","企业缴纳合计(元)",{}),
  ]},
"awards_penalties": {
  "desc": "奖惩记录: 员工奖励与处罚",
  "pk": "record_id",
  "ref": {"schema": "public", "table": "awards_penalties"},
  "cols": [
    ("record_id","INTEGER","记录ID(主键)",{"not_null": True}),
    ("emp_id","INTEGER","员工ID",{}),
    ("record_date","DATE","记录日期",{}),
    ("record_type","VARCHAR","类型: 奖励/处罚",{}),
    ("category","VARCHAR","类别: 奖金/通报表扬/优秀员工/警告/记过/罚款",{}),
    ("amount","DECIMAL(10,2)","金额(元), 通报类为空",{}),
    ("reason","VARCHAR","事由",{}),
    ("approver_id","INTEGER","审批人员工ID",{}),
  ]},
"overtime_requests": {
  "desc": "加班申请与审批",
  "pk": "ot_id",
  "ref": {"schema": "public", "table": "overtime_requests"},
  "cols": [
    ("ot_id","INTEGER","加班单ID(主键)",{"not_null": True}),
    ("emp_id","INTEGER","员工ID",{}),
    ("ot_date","DATE","加班日期",{}),
    ("planned_hours","DECIMAL(4,1)","申请加班时长(小时)",{}),
    ("actual_hours","DECIMAL(4,1)","实际加班时长(小时), 未完成为空",{}),
    ("reason","VARCHAR","加班事由",{}),
    ("status","VARCHAR","审批状态: 已批准/已拒绝/待审批",{}),
    ("compensation","VARCHAR","补偿方式: 调休/加班费/无",{}),
    ("applied_at","DATE","申请日期(加班前)",{}),
  ]},
"leave_balances": {
  "desc": "假期余额季度快照: 年假与调休",
  "pk": "balance_id",
  "ref": {"schema": "public", "table": "leave_balances"},
  "cols": [
    ("balance_id","INTEGER","余额记录ID(主键)",{"not_null": True}),
    ("emp_id","INTEGER","员工ID",{}),
    ("balance_type","VARCHAR","假期类型: 年假/调休",{}),
    ("as_of_quarter","VARCHAR","快照季度, 格式如 2025Q3",{}),
    ("entitled","DECIMAL(5,1)","本期总额度(天)",{}),
    ("used","DECIMAL(5,1)","已使用(天, 截至季度末)",{}),
    ("remaining","DECIMAL(5,1)","剩余(天)",{}),
    ("expired","DECIMAL(5,1)","本期已失效(天, 年度清零)",{}),
  ]},
"offers": {
  "desc": "Offer发放记录",
  "pk": "offer_id",
  "ref": {"schema": "public", "table": "offers"},
  "cols": [
    ("offer_id","INTEGER","Offer ID(主键)",{"not_null": True}),
    ("cand_id","INTEGER","候选人ID",{}),
    ("offer_date","DATE","发放日期",{}),
    ("offer_salary","DECIMAL(10,2)","Offer 月薪(元)",{}),
    ("status","VARCHAR","状态: 待回复/已接受/已拒绝",{}),
    ("response_date","DATE","回复日期",{}),
    ("reject_reason","VARCHAR","拒绝原因: 薪酬不匹配/已接其他offer/家庭原因/其他",{}),
  ]},
"recruitment_costs": {
  "desc": "招聘渠道月度费用",
  "pk": "cost_id",
  "ref": {"schema": "public", "table": "recruitment_costs"},
  "cols": [
    ("cost_id","INTEGER","费用记录ID(主键)",{"not_null": True}),
    ("channel","VARCHAR","渠道: 招聘网站/猎头/内推奖励/校园招聘",{}),
    ("cost_month","VARCHAR","费用月份, 格式 YYYY-MM",{}),
    ("amount","DECIMAL(12,2)","费用金额(元)",{}),
    ("notes","VARCHAR","备注",{}),
  ]},
"performance_goals": {
  "desc": "绩效目标: 员工半年度 KPI/OKR 及完成率",
  "pk": "goal_id",
  "ref": {"schema": "public", "table": "performance_goals"},
  "cols": [
    ("goal_id","INTEGER","目标ID(主键)",{"not_null": True}),
    ("emp_id","INTEGER","员工ID",{}),
    ("review_period","VARCHAR","考核周期, 格式如 2025H1",{}),
    ("goal_type","VARCHAR","目标类型: KPI/OKR",{}),
    ("goal_desc","VARCHAR","目标描述",{}),
    ("weight","INTEGER","权重(%)",{}),
    ("completion_pct","DECIMAL(5,1)","完成率(%)",{}),
  ]},
"talent_pool": {
  "desc": "人才池: 高潜人才与关键岗位继任者",
  "pk": "pool_id",
  "ref": {"schema": "public", "table": "talent_pool"},
  "cols": [
    ("pool_id","INTEGER","记录ID(主键)",{"not_null": True}),
    ("emp_id","INTEGER","员工ID",{}),
    ("pool_type","VARCHAR","类型: 高潜人才/继任者",{}),
    ("target_position","VARCHAR","继任目标岗位, 继任者必填",{}),
    ("potential_rating","VARCHAR","潜力评级: 高潜/潜力之星",{}),
    ("nominated_date","DATE","提名日期",{}),
    ("nominated_by","INTEGER","提名人员工ID",{}),
    ("status","VARCHAR","状态: 在池/已晋升/已移出",{}),
  ]},
"engagement_surveys": {
  "desc": "敬业度调研: 年度全员调研(1-5分)",
  "pk": "survey_id",
  "ref": {"schema": "public", "table": "engagement_surveys"},
  "cols": [
    ("survey_id","INTEGER","调研记录ID(主键)",{"not_null": True}),
    ("emp_id","INTEGER","员工ID",{}),
    ("survey_year","INTEGER","调研年度",{}),
    ("engagement_score","DECIMAL(3,1)","敬业度总分(1-5)",{}),
    ("recognition","DECIMAL(3,1)","认可与回报维度(1-5)",{}),
    ("growth","DECIMAL(3,1)","成长与发展维度(1-5)",{}),
    ("pay_satisfaction","DECIMAL(3,1)","薪酬满意度维度(1-5)",{}),
    ("manager_trust","DECIMAL(3,1)","管理者信任维度(1-5)",{}),
    ("work_life_balance","DECIMAL(3,1)","工作生活平衡维度(1-5)",{}),
  ]},
"exit_interviews": {
  "desc": "离职面谈: 离职员工真实原因调研",
  "pk": "exit_id",
  "ref": {"schema": "public", "table": "exit_interviews"},
  "cols": [
    ("exit_id","INTEGER","面谈记录ID(主键)",{"not_null": True}),
    ("emp_id","INTEGER","离职员工ID",{}),
    ("interview_date","DATE","面谈日期(离职前一周内)",{}),
    ("real_reason_category","VARCHAR","真实原因归类: 薪酬福利/职业发展/管理问题/工作文化/家庭个人/健康",{}),
    ("satisfaction","DECIMAL(3,1)","公司满意度(1-5)",{}),
    ("would_recommend","BOOLEAN","是否愿意推荐他人入职",{}),
    ("comment","VARCHAR","面谈备注",{}),
  ]},
}

def col_yaml(name, typ, desc, opts):
    lines = [f"  - name: {name}", f"    type: {typ}"]
    if "not_null" in opts: lines.append("    not_null: true")
    if "calc" in opts:
        lines.append("    is_calculated: true")
        lines.append(f'    expression: "{opts["calc"]}"')
    lines.append("    properties:")
    lines.append(f'      description: "{desc}"')
    return "\n".join(lines)

for name, m in MODELS.items():
    d = pathlib.Path("models") / name
    d.mkdir(parents=True, exist_ok=True)
    parts = [f"name: {name}", "table_reference:", '  catalog: ""',
             f'  schema: {m["ref"]["schema"]}', f'  table: {m["ref"]["table"]}',
             f"primary_key: {m['pk']}", "properties:", f'  description: "{m["desc"]}"', "columns:"]
    parts += [col_yaml(*c) for c in m["cols"]]
    (d / "metadata.yml").write_text("\n".join(parts) + "\n", encoding="utf-8")
    print("model:", name)
