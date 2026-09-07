#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HR v2 扩展数据生成器 (GOAL.md M1)
- 读取 seed/out/*.csv (Phase 1 基线数据, 随机种子42), 独立随机种子 2026 → 基线零回归
- 生成 seed/out2/*.csv: 编制规划/晋升/合同/调薪/社保/奖惩/加班/假期余额/offer/渠道费用/绩效目标/人才池/敬业度/离职面谈
- 业务规律: 晋升与S/A绩效相关; 敬业度低的员工次年离职概率更高; offer拒绝与薪酬竞争力负相关;
  内推奖励=内推入职×2000; 猎头费用尖峰; 校招费用金三银四/金九银十季节性
"""
import csv, os, random, datetime as dt

R = random.Random(2026)
BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out2")
os.makedirs(OUT, exist_ok=True)
REF = dt.date(2026, 9, 1)

def read(name):
    with open(os.path.join(BASE, name), encoding="utf-8") as f:
        return list(csv.DictReader(f))

def d(s):
    return dt.date.fromisoformat(s) if s else None

def rand_workday(d0, d1):
    if d0 > d1:
        d0 = d1
    for _ in range(30):
        c = d0 + dt.timedelta(days=R.randint(0, max((d1 - d0).days, 0)))
        if c.weekday() < 5:
            return c
    return d0

def month_days(y_, m_):
    nxt = dt.date(y_ + (m_ == 12), (m_ % 12) + 1, 1)
    return (nxt - dt.date(y_, m_, 1)).days

def mrw(y_, m_):
    return rand_workday(dt.date(y_, m_, 1), dt.date(y_, m_, month_days(y_, m_)))

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

employees = read("employees.csv")
departments = read("departments.csv")
reviews = read("performance_reviews.csv")
candidates = read("candidates.csv")
leaves = read("leave_requests.csv")
salary_rows = read("salary_payments.csv")

for e in employees:
    e["emp_id"] = int(e["emp_id"]); e["dept_id"] = int(e["dept_id"])
    e["hire_date"] = d(e["hire_date"]); e["termination_date"] = d(e["termination_date"])
    e["base_salary"] = float(e["base_salary"])
for r_ in reviews:
    r_["emp_id"] = int(r_["emp_id"]); r_["score"] = float(r_["score"])
for i, c in enumerate(candidates, 1):
    c["cand_id"] = i
    c["opening_id"] = int(c["opening_id"])
    c["applied_at"] = d(c["applied_at"])
    c["expected_salary"] = float(c["expected_salary"]) if c["expected_salary"] else None
    c["hired_emp_id"] = int(c["hired_emp_id"]) if c["hired_emp_id"] else None
for l in leaves:
    l["emp_id"] = int(l["emp_id"]); l["days"] = float(l["days"]); l["start_date"] = d(l["start_date"])
for s in salary_rows:
    s["emp_id"] = int(s["emp_id"]); s["base_pay"] = float(s["base_pay"])

active = [e for e in employees if e["status"] == "在职"]
terminated = [e for e in employees if e["status"] == "离职"]
by_id = {e["emp_id"]: e for e in employees}
LEVELS = ["初级", "中级", "高级", "专家", "总监", "副总裁"]
DIRECTORS = [e for e in employees if e["job_level"] == "总监"]

def director_of(dept_id):
    ds = [x for x in DIRECTORS if x["dept_id"] == dept_id]
    return R.choice(ds)["emp_id"] if ds else None

def grade_of(emp_id, period):
    for r_ in reviews:
        if r_["emp_id"] == emp_id and r_["review_period"] == period:
            return r_["grade"]
    return None

def base_at(e, at):
    """与 salary_payments 同口径的历史基本工资倒推 (按年3%)"""
    y_ = max(0.0, (dt.date(2026, 9, 1) - at).days / 365.25)
    return max(4000, round(e["base_salary"] * (1.03 ** (-y_)) / 100) * 100)

def active_at(e, day):
    return e["hire_date"] <= day and (e["termination_date"] is None or e["termination_date"] >= day)

# ---------------- 1. 编制规划 headcount_plan ----------------
headcount_plan = []
for year in (2023, 2024, 2025, 2026):
    ye = dt.date(year, 12, 31)
    for dept in departments:
        did = int(dept["dept_id"])
        actual = sum(1 for e in employees if e["dept_id"] == did and active_at(e, ye))
        planned = max(1, round(actual * R.uniform(0.88, 1.15)))
        avg_sal = R.uniform(15000, 24000)
        budget = round(planned * avg_sal * 12 * 1.35 / 10000) * 10000
        headcount_plan.append({"plan_year": year, "dept_id": did, "planned_headcount": planned,
                               "budget_labor_cost": float(budget),
                               "approved_at": dt.date(year - 1, 11, R.randint(1, 28))})

# ---------------- 2. 晋升 promotions ----------------
promotions = []
promoted_set = {}
for year in (2023, 2024, 2025, 2026):
    pool = []
    for e in active + terminated:
        if e["job_level"] not in ("初级", "中级", "高级"):
            continue
        promo_window = dt.date(year, 3, 31)
        if (promo_window - e["hire_date"]).days < 550:      # 司龄≥1.5年
            continue
        if e["termination_date"] and e["termination_date"] < promo_window:
            continue
        prev_grade = grade_of(e["emp_id"], f"{year - 1}H2") or grade_of(e["emp_id"], f"{year - 1}H1")
        w = {"S": 40, "A": 22, "B": 8, "C": 3, "D": 1}.get(prev_grade, 6)
        pool.append((e, w))
    if not pool:
        continue
    n = max(1, int(len(pool) * 0.07))
    chosen = set()
    while len(chosen) < n:
        xs, ws = zip(*pool)
        e = R.choices(xs, weights=ws, k=1)[0]
        if e["emp_id"] in chosen:
            continue
        chosen.add(e["emp_id"])
        idx = LEVELS.index(e["job_level"])
        promo_date = mrw(year, 3)
        sb = base_at(e, promo_date)
        sa = round(sb * R.uniform(1.18, 1.32) / 100) * 100
        promotions.append({"emp_id": e["emp_id"], "promo_date": promo_date,
                           "from_level": e["job_level"], "to_level": LEVELS[idx + 1],
                           "from_title": e["job_title"],
                           "to_title": ("高级" + e["job_title"].replace("高级", "")) if e["job_level"] == "中级" else e["job_title"],
                           "salary_before": float(sb), "salary_after": float(sa),
                           "reason": weighted_reason if (weighted_reason := R.choices(
                               ["年度晋升", "破格晋升", "继任就任"], weights=[75, 15, 10])[0]) else "年度晋升"})
        promoted_set[e["emp_id"]] = promo_date

# ---------------- 3. 劳动合同 contracts ----------------
contracts = []
cid = 0
for e in employees:
    hire = e["hire_date"]
    if e["employment_type"] == "实习":
        rows_ = [("实习协议", hire, hire + dt.timedelta(days=182))]
    elif e["employment_type"] == "外包":
        rows_ = [("劳务协议", hire, hire + dt.timedelta(days=365))]
    else:
        end1 = hire + dt.timedelta(days=3 * 365)
        tenure_days = ((e["termination_date"] or REF) - hire).days
        if tenure_days > 3 * 365 + 180:
            if e["job_level"] in ("专家", "总监", "副总裁") or R.random() < 0.12:
                rows_ = [("固定期限", hire, end1), ("无固定期限", end1 + dt.timedelta(days=1), None)]
            else:
                rows_ = [("固定期限", hire, end1), ("固定期限", end1 + dt.timedelta(days=1), end1 + dt.timedelta(days=5 * 365))]
        else:
            rows_ = [("固定期限", hire, end1)]
    renew = 0
    for (ctype, cstart, cend) in rows_:
        cid += 1
        if e["termination_date"] and cstart <= e["termination_date"]:
            status = "已解除"
            cend_eff = min(cend, e["termination_date"]) if cend else e["termination_date"]
        elif cend and cend < REF:
            status, cend_eff = "已到期", cend
        else:
            status, cend_eff = "履行中", cend
            if status == "履行中" and cend_eff and cend_eff <= REF + dt.timedelta(days=100) and R.random() < 0.5:
                cend_eff = REF + dt.timedelta(days=R.randint(15, 88))   # 制造续签窗口期合同
        contracts.append({"contract_no": f"HT{cid:05d}", "emp_id": e["emp_id"], "contract_type": ctype,
                          "start_date": cstart, "end_date": cend_eff, "renewals": renew,
                          "status": status, "signed_date": cstart - dt.timedelta(days=R.randint(0, 15))})
        renew += 1

# ---------------- 4. 调薪 salary_changes ----------------
salary_changes = []
for (yy, mm) in [(y_, m_) for y_ in (2023, 2024, 2025, 2026) for m_ in (4,)]:
    eff = mrw(yy, mm)
    for e in employees:
        if not active_at(e, eff) or (eff - e["hire_date"]).days < 300:
            continue
        if R.random() < 0.65:
            sb = base_at(e, eff)
            sa = round(sb * R.uniform(1.02, 1.08) / 100) * 100
            salary_changes.append({"emp_id": e["emp_id"], "effective_date": eff,
                                   "salary_before": float(sb), "salary_after": float(sa),
                                   "change_pct": round((sa / sb - 1) * 100, 2), "change_type": "年度调薪"})
for p in promotions:  # 晋升调薪
    sb = p["salary_before"]
    salary_changes.append({"emp_id": p["emp_id"], "effective_date": p["promo_date"],
                           "salary_before": sb, "salary_after": p["salary_after"],
                           "change_pct": round((p["salary_after"] / sb - 1) * 100, 2), "change_type": "晋升调薪"})
for e in employees:   # 特批调薪 ~2%
    if e["status"] != "在职" or R.random() > 0.02:
        continue
    eff = rand_workday(dt.date(2024, 1, 1), dt.date(2026, 7, 31))
    sb = base_at(e, eff)
    sa = round(sb * R.uniform(1.05, 1.15) / 100) * 100
    salary_changes.append({"emp_id": e["emp_id"], "effective_date": eff,
                           "salary_before": float(sb), "salary_after": float(sa),
                           "change_pct": round((sa / sb - 1) * 100, 2), "change_type": "特批调薪"})

# ---------------- 5. 社保公积金 insurance_payments ----------------
RATE = [("pension", .16), ("medical", .095), ("unemployment", .005),
        ("injury", .004), ("maternity", .008), ("housing_fund", .08)]
insurance = []
for s in salary_rows:
    comps = {k: round(s["base_pay"] * v, 2) for k, v in RATE}
    insurance.append({"emp_id": s["emp_id"], "pay_period": s["pay_period"], **comps,
                      "company_total": round(sum(comps.values()), 2)})

# ---------------- 6. 奖惩 awards_penalties ----------------
awards = []
for r_ in reviews:
    e = by_id[r_["emp_id"]]
    yy = int(r_["review_period"][:4])
    if r_["grade"] in ("S", "A") and R.random() < 0.6:
        d0 = dt.date(yy + 1, 1, 1)
        if d0 > REF:
            continue
        cash = r_["grade"] == "S" or R.random() < 0.5
        awards.append({"emp_id": e["emp_id"], "record_date": rand_workday(d0, dt.date(yy + 1, 2, 28)),
                       "record_type": "奖励",
                       "category": "奖金" if cash else R.choice(["通报表扬", "优秀员工"]),
                       "amount": float(round(R.uniform(1000, 5000), 2)) if cash else None,
                       "reason": f"{r_['review_period']}绩效{r_['grade']}级",
                       "approver_id": e["manager_id"] or ""})
    elif r_["grade"] in ("C", "D") and R.random() < 0.18:
        d0 = dt.date(yy, 7, 1)
        if d0 > REF:
            continue
        awards.append({"emp_id": e["emp_id"], "record_date": rand_workday(d0, dt.date(yy, 12, 31)),
                       "record_type": "处罚", "category": R.choice(["警告", "记过"]),
                       "amount": None,
                       "reason": R.choice(["阶段性目标未达成", "考勤纪律问题", "工作失误造成返工"]),
                       "approver_id": e["manager_id"] or ""})

# ---------------- 7. 加班申请 overtime_requests ----------------
overtime = []
for e in employees:
    p_hc = 0.35 if e["dept_id"] in (1, 3, 6) else 0.15
    for (yy, mm) in [(y_, m_) for y_ in (2025, 2026) for m_ in range(1, 13)]:
        if (yy, mm) > (2026, 8) or not active_at(e, dt.date(yy, mm, month_days(yy, mm))):
            continue
        if R.random() < p_hc:
            ot_day = mrw(yy, mm)
            if ot_day < e["hire_date"] or (e["termination_date"] and ot_day > e["termination_date"]):
                continue
            planned = R.choice([2, 3, 4, 4, 6, 8])
            st = R.choices(["已批准", "已拒绝", "待审批"], weights=[85, 10, 5])[0]
            overtime.append({"emp_id": e["emp_id"], "ot_date": ot_day, "planned_hours": planned,
                             "actual_hours": planned if st != "已拒绝" else None,
                             "reason": R.choice(["项目上线冲刺", "紧急故障处理", "月度结账", "客户交付",
                                                 "版本发布", "季度盘点"]),
                             "status": st,
                             "compensation": R.choices(["调休", "加班费", "无"], weights=[60, 35, 5])[0] if st == "已批准" else "无",
                             "applied_at": ot_day - dt.timedelta(days=R.randint(1, 5))})

# ---------------- 8. 假期余额 leave_balances ----------------
leave_balances = []
QUARTERS = [(y_, q_) for y_ in (2025, 2026) for q_ in (1, 2, 3) if (y_, q_) != (2026, 3) or True]
QUARTERS = [(2025, 1), (2025, 2), (2025, 3), (2026, 1), (2026, 2), (2026, 3)]
def qend(y_, q_):
    return dt.date(y_, q_ * 3, month_days(y_, q_ * 3))
ot_comp_days = {}
for o in overtime:
    if o["compensation"] == "调休" and o["status"] == "已批准":
        ot_comp_days.setdefault(o["emp_id"], 0.0)
        ot_comp_days[o["emp_id"]] += o["planned_hours"] / 8.0
for e in employees:
    tenure = clamp(int(((e["termination_date"] or REF) - e["hire_date"]).days / 365.25), 0, 10)
    entitled_al = 5 + min(10, tenure)
    for (yy, q_) in QUARTERS:
        qe = qend(yy, q_)
        if not active_at(e, qe) or e["hire_date"] > qe:
            continue
        used_al = sum(l["days"] for l in leaves if l["emp_id"] == e["emp_id"] and l["leave_type"] == "年假"
                      and l["status"] == "已批准" and l["start_date"].year == yy and l["start_date"] <= qe)
        used_tw = sum(l["days"] for l in leaves if l["emp_id"] == e["emp_id"] and l["leave_type"] == "调休"
                      and l["status"] == "已批准" and l["start_date"].year == yy and l["start_date"] <= qe)
        expired_al = max(0.0, entitled_al - used_al) if (yy == 2026 and q_ == 1) else 0.0
        leave_balances.append({"emp_id": e["emp_id"], "balance_type": "年假", "as_of_quarter": f"{yy}Q{q_}",
                               "entitled": float(entitled_al), "used": round(used_al, 1),
                               "remaining": round(max(0.0, entitled_al - used_al), 1), "expired": round(expired_al, 1)})
        comp = ot_comp_days.get(e["emp_id"], 0.0)
        leave_balances.append({"emp_id": e["emp_id"], "balance_type": "调休", "as_of_quarter": f"{yy}Q{q_}",
                               "entitled": round(comp, 1), "used": round(used_tw, 1),
                               "remaining": round(max(0.0, comp - used_tw), 1), "expired": 0.0})

# ---------------- 9. Offer 记录 offers ----------------
offers = []
for c in candidates:
    if c["stage"] not in ("已入职", "已发offer", "拒绝offer") or not c["expected_salary"]:
        continue
    od = c["applied_at"] + dt.timedelta(days=R.randint(12, 25))
    if od >= REF:
        continue
    if c["stage"] == "已入职":
        st, resp, reason = "已接受", od + dt.timedelta(days=R.randint(1, 5)), None
    elif c["stage"] == "已发offer":
        st, resp, reason = "待回复", None, None
    else:
        st = "已拒绝"
        resp = od + dt.timedelta(days=R.randint(2, 8))
        reason = R.choices(["薪酬不匹配", "已接其他offer", "家庭原因", "其他"], weights=[40, 30, 18, 12])[0]
    offers.append({"cand_id": c["cand_id"], "offer_date": od, "offer_salary": c["expected_salary"],
                   "status": st, "response_date": resp, "reject_reason": reason})

# ---------------- 10. 招聘渠道费用 recruitment_costs ----------------
hires_by_month_source = {}
for c in candidates:
    if c["stage"] == "已入职" and c["hired_emp_id"]:
        hd = by_id[c["hired_emp_id"]]["hire_date"]
        hires_by_month_source.setdefault((hd.year, hd.month, c["source"]), 0)
        hires_by_month_source[(hd.year, hd.month, c["source"])] += 1
costs = []
for (yy, mm) in [(y_, m_) for y_ in (2023, 2024, 2025, 2026) for m_ in range(1, 13)]:
    if (yy, mm) > (2026, 8):
        continue
    period = f"{yy:04d}-{mm:02d}"
    costs.append({"channel": "招聘网站", "cost_month": period, "amount": float(round(R.uniform(8000, 15000), 2)),
                  "notes": "年度框架会员费分摊"})
    if R.random() < 0.25:
        costs.append({"channel": "猎头", "cost_month": period,
                      "amount": float(round(R.uniform(15000, 40000), 2)), "notes": "按到岗候选人服务费"})
    seasonal = 2.5 if mm in (3, 4, 9, 10) else 1.0
    costs.append({"channel": "校园招聘", "cost_month": period,
                  "amount": float(round(R.uniform(2000, 8000) * seasonal, 2)), "notes": "宣讲会与双选会费用"})
    n_ref = hires_by_month_source.get((yy, mm, "内推"), 0)
    if n_ref:
        costs.append({"channel": "内推奖励", "cost_month": period, "amount": float(n_ref * 2000),
                      "notes": f"内推入职{n_ref}人×2000元"})

# ---------------- 11. 绩效目标 performance_goals ----------------
GOAL_TPL = {
    1: ["核心模块交付及时率≥95%", "线上故障数同比下降20%", "完成关键技术方案设计并通过评审", "带教1名新成员通过转正"],
    4: ["季度销售目标达成率≥90%", "新客户开发≥5家", "老客户续约率≥85%", "回款及时率≥95%"],
}
GENERIC = ["季度关键任务按期交付", "跨部门协作满意度≥4分", "输出流程优化提案≥1项", "完成专业分享≥2次", "关键差错率为0"]
goals = []
for period in ("2024H1", "2024H2", "2025H1", "2025H2", "2026H1"):
    yy = int(period[:4])
    end = dt.date(yy, 6, 30) if period.endswith("H1") else dt.date(yy, 12, 31)
    g = grade_of(0, "")  # noop
    for e in employees:
        if not active_at(e, end - dt.timedelta(days=30)) or (end - e["hire_date"]).days < 60:
            continue
        grade = grade_of(e["emp_id"], period)
        base_c = {"S": 88, "A": 78, "B": 65, "C": 50, "D": 35}.get(grade, 68)
        n = R.randint(2, 4)
        wlist = [10] * n
        for _ in range((100 - 10 * n) // 10):
            wlist[R.randrange(n)] += 10
        R.shuffle(wlist)
        tpl = GOAL_TPL.get(e["dept_id"], GENERIC)
        picks = R.sample(tpl, min(n, len(tpl)))
        while len(picks) < n:
            picks.append(R.choice(GENERIC) + f"({R.randint(2,9)})")
        for i in range(n):
            goals.append({"emp_id": e["emp_id"], "review_period": period,
                          "goal_type": R.choices(["KPI", "OKR"], weights=[70, 30])[0],
                          "goal_desc": picks[i], "weight": wlist[i],
                          "completion_pct": round(clamp(R.gauss(base_c, 12), 15, 100), 1)})

# ---------------- 12. 人才池 talent_pool ----------------
talent = []
review_by_emp = {}
for r_ in reviews:
    review_by_emp.setdefault(r_["emp_id"], []).append(r_)
for e in active:
    if e["job_level"] not in ("初级", "中级", "高级") or R.random() > 0.09:
        continue
    recent = [r_ for r_ in review_by_emp.get(e["emp_id"], []) if r_["review_period"] in ("2025H2", "2026H1")]
    if not recent or not any(r_["grade"] in ("S", "A") for r_ in recent):
        continue
    is_successor = R.random() < 0.3
    dept_name = next(x["dept_name"] for x in departments if int(x["dept_id"]) == e["dept_id"])
    talent.append({"emp_id": e["emp_id"],
                   "pool_type": "继任者" if is_successor else "高潜人才",
                   "target_position": f"{dept_name.replace('部', '')}总监" if is_successor else None,
                   "potential_rating": R.choices(["高潜", "潜力之星"], weights=[60, 40])[0],
                   "nominated_date": rand_workday(dt.date(2024, 6, 1), dt.date(2026, 6, 30)),
                   "nominated_by": director_of(e["dept_id"]) or "",
                   "status": "已晋升" if e["emp_id"] in promoted_set else
                             R.choices(["在池", "已移出"], weights=[92, 8])[0]})
talent = talent[:60]

# ---------------- 13. 敬业度调研 engagement_surveys ----------------
surveys = []
for yy in (2023, 2024, 2025, 2026):
    survey_day = dt.date(yy, 6, 1)
    for e in active + terminated:
        if not active_at(e, survey_day) or R.random() > 0.85:
            continue
        leaving_soon = e["termination_date"] and 0 <= (e["termination_date"] - survey_day).days <= 365
        b = R.gauss(3.3, 0.45) - (0.5 if leaving_soon else 0)
        dims = [clamp(R.gauss(b + R.uniform(-0.2, 0.2), 0.3), 1, 5) for _ in range(5)]
        eng = round(clamp(sum(dims) / 5, 1, 5), 1)
        surveys.append({"emp_id": e["emp_id"], "survey_year": yy, "engagement_score": eng,
                        "recognition": round(dims[0], 1), "growth": round(dims[1], 1),
                        "pay_satisfaction": round(dims[2], 1), "manager_trust": round(dims[3], 1),
                        "work_life_balance": round(dims[4], 1)})

# ---------------- 14. 离职面谈 exit_interviews ----------------
exits = []
for e in terminated:
    if e["termination_date"] < dt.date(2023, 2, 1) or R.random() > 0.75:
        continue
    ivd = rand_workday(e["termination_date"] - dt.timedelta(days=6), e["termination_date"])
    real = R.choices(["薪酬福利", "职业发展", "管理问题", "工作文化", "家庭个人", "健康"],
                     weights=[30, 25, 13, 8, 16, 8])[0]
    sat = round(clamp(R.gauss(2.9, 1.0), 1, 5), 1)
    exits.append({"emp_id": e["emp_id"], "interview_date": ivd, "real_reason_category": real,
                  "satisfaction": sat, "would_recommend": "t" if (sat >= 3) == (R.random() < 0.7) else "f",
                  "comment": R.choice(["直属上级管理风格是主要考虑因素", "外部机会薪酬高出30%以上",
                                       "晋升通道不透明，看不到发展空间", "家庭原因需要回老家",
                                       "工作强度长期偏高", "认同公司文化，个人规划调整"])})

# ---------------- 写 CSV ----------------
def write(name, header, rows, fmt):
    with open(os.path.join(OUT, name), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r_ in rows:
            w.writerow(fmt(r_))

write("headcount_plan.csv", ["plan_year", "dept_id", "planned_headcount", "budget_labor_cost", "approved_at"],
      headcount_plan, lambda r: [r["plan_year"], r["dept_id"], r["planned_headcount"], r["budget_labor_cost"], r["approved_at"]])
write("promotions.csv", ["emp_id", "promo_date", "from_level", "to_level", "from_title", "to_title",
                         "salary_before", "salary_after", "reason"],
      promotions, lambda r: [r["emp_id"], r["promo_date"], r["from_level"], r["to_level"], r["from_title"],
                             r["to_title"], r["salary_before"], r["salary_after"], r["reason"]])
write("contracts.csv", ["contract_no", "emp_id", "contract_type", "start_date", "end_date", "renewals",
                        "status", "signed_date"],
      contracts, lambda r: [r["contract_no"], r["emp_id"], r["contract_type"], r["start_date"],
                            r["end_date"] or "", r["renewals"], r["status"], r["signed_date"]])
write("salary_changes.csv", ["emp_id", "effective_date", "salary_before", "salary_after", "change_pct", "change_type"],
      salary_changes, lambda r: [r["emp_id"], r["effective_date"], r["salary_before"], r["salary_after"],
                                 r["change_pct"], r["change_type"]])
write("insurance_payments.csv", ["emp_id", "pay_period", "pension", "medical", "unemployment", "injury",
                                 "maternity", "housing_fund", "company_total"],
      insurance, lambda r: [r["emp_id"], r["pay_period"], r["pension"], r["medical"], r["unemployment"],
                            r["injury"], r["maternity"], r["housing_fund"], r["company_total"]])
write("awards_penalties.csv", ["emp_id", "record_date", "record_type", "category", "amount", "reason", "approver_id"],
      awards, lambda r: [r["emp_id"], r["record_date"], r["record_type"], r["category"],
                         r["amount"] if r["amount"] != "" and r["amount"] is not None else "",
                         r["reason"], r["approver_id"]])
write("overtime_requests.csv", ["emp_id", "ot_date", "planned_hours", "actual_hours", "reason", "status",
                                "compensation", "applied_at"],
      overtime, lambda r: [r["emp_id"], r["ot_date"], r["planned_hours"],
                           r["actual_hours"] if r["actual_hours"] is not None else "", r["reason"],
                           r["status"], r["compensation"], r["applied_at"]])
write("leave_balances.csv", ["emp_id", "balance_type", "as_of_quarter", "entitled", "used", "remaining", "expired"],
      leave_balances, lambda r: [r["emp_id"], r["balance_type"], r["as_of_quarter"], r["entitled"], r["used"],
                                 r["remaining"], r["expired"]])
write("offers.csv", ["cand_id", "offer_date", "offer_salary", "status", "response_date", "reject_reason"],
      offers, lambda r: [r["cand_id"], r["offer_date"], r["offer_salary"], r["status"],
                         r["response_date"] or "", r["reject_reason"] or ""])
write("recruitment_costs.csv", ["channel", "cost_month", "amount", "notes"],
      costs, lambda r: [r["channel"], r["cost_month"], r["amount"], r["notes"]])
write("performance_goals.csv", ["emp_id", "review_period", "goal_type", "goal_desc", "weight", "completion_pct"],
      goals, lambda r: [r["emp_id"], r["review_period"], r["goal_type"], r["goal_desc"], r["weight"], r["completion_pct"]])
write("talent_pool.csv", ["emp_id", "pool_type", "target_position", "potential_rating", "nominated_date",
                          "nominated_by", "status"],
      talent, lambda r: [r["emp_id"], r["pool_type"], r["target_position"] or "", r["potential_rating"],
                         r["nominated_date"], r["nominated_by"], r["status"]])
write("engagement_surveys.csv", ["emp_id", "survey_year", "engagement_score", "recognition", "growth",
                                 "pay_satisfaction", "manager_trust", "work_life_balance"],
      surveys, lambda r: [r["emp_id"], r["survey_year"], r["engagement_score"], r["recognition"], r["growth"],
                          r["pay_satisfaction"], r["manager_trust"], r["work_life_balance"]])
write("exit_interviews.csv", ["emp_id", "interview_date", "real_reason_category", "satisfaction",
                              "would_recommend", "comment"],
      exits, lambda r: [r["emp_id"], r["interview_date"], r["real_reason_category"], r["satisfaction"],
                        r["would_recommend"], r["comment"]])

print(f"headcount_plan     {len(headcount_plan)}")
print(f"promotions         {len(promotions)}")
print(f"contracts          {len(contracts)}")
print(f"salary_changes     {len(salary_changes)}")
print(f"insurance_payments {len(insurance)}")
print(f"awards_penalties   {len(awards)}")
print(f"overtime_requests  {len(overtime)}")
print(f"leave_balances     {len(leave_balances)}")
print(f"offers             {len(offers)}")
print(f"recruitment_costs  {len(costs)}")
print(f"performance_goals  {len(goals)}")
print(f"talent_pool        {len(talent)}")
print(f"engagement_surveys {len(surveys)}")
print(f"exit_interviews    {len(exits)}")
