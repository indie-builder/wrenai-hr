#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
星辰科技 HR 演示数据生成器
- 固定随机种子, 完全可复现
- 输出 CSV 到同目录 out/, 由数据库装载脚本装载
- 业务规律: 薪酬与职级/城市/部门挂钩, 离职率与司龄/绩效/部门相关,
  招聘金三银四季节性, 年终奖次年1月发放, 候选人漏斗与入职员工闭环关联
"""
import os, random, datetime as dt

from seed_common import month_days, rand_workday, month_rand_workday, weighted, write_csv

random.seed(42)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT, exist_ok=True)

REF = dt.date(2026, 9, 1)          # 数据截至 2026-08-31
MONTHS = [(2023 + i // 12, i % 12 + 1) for i in range(44)]   # 2023-01 .. 2026-08

# ---------------- 基础字典 ----------------
DEPTS = [  # (名称, 办公地, 薪酬系数)
    ("技术部", "北京", 1.15), ("产品部", "北京", 1.05), ("运营部", "北京", 1.00),
    ("销售部", "上海", 0.90), ("市场部", "上海", 0.95), ("客服部", "成都", 0.80),
    ("人事部", "北京", 0.90), ("财务部", "北京", 0.90), ("总经办", "北京", 1.20),
]
DEPT_W = {"技术部": 34, "产品部": 8, "运营部": 10, "销售部": 16, "市场部": 7,
          "客服部": 12, "人事部": 5, "财务部": 4, "总经办": 0}

TITLES = {
    "技术部": ["软件工程师", "高级软件工程师", "测试工程师", "运维工程师", "数据工程师", "算法工程师", "架构师"],
    "产品部": ["产品经理", "高级产品经理", "产品运营", "UI设计师"],
    "运营部": ["运营专员", "用户运营", "数据分析师", "活动策划"],
    "销售部": ["销售代表", "大客户经理", "销售运营", "渠道经理"],
    "市场部": ["市场专员", "品牌经理", "内容运营", "市场分析师"],
    "客服部": ["客服代表", "客服主管", "质检专员"],
    "人事部": ["HR专员", "HRBP", "招聘专员", "培训专员", "薪酬绩效专员"],
    "财务部": ["会计", "财务分析师", "出纳", "税务专员"],
    "总经办": ["总经理助理"],
}
LEVELS = ["初级", "中级", "高级", "专家", "总监", "副总裁"]
LEVEL_SALARY = {  # (下限k, 上限k) 月基本工资
    "初级": (9, 14), "中级": (15, 22), "高级": (23, 32),
    "专家": (33, 48), "总监": (45, 65), "副总裁": (70, 90),
}
GENDER_W = [("男", 55), ("女", 45)]
SOURCE_W = [("内推", 25), ("招聘网站", 45), ("猎头", 12), ("校园招聘", 18)]
CITY_MULT = {"北京": 1.05, "上海": 1.00, "深圳": 0.95, "杭州": 0.90, "成都": 0.75, "远程": 0.85}
CITIES = ["北京", "上海", "深圳", "杭州", "成都", "远程"]
SURNAME = "王李张刘陈杨黄赵吴周徐孙马朱胡郭何林罗高郑梁谢宋唐许韩冯邓曹彭曾肖田董潘袁蔡蒋余杜叶程魏苏吕"
MALE_GIVEN = ["伟", "强", "磊", "军", "洋", "勇", "杰", "涛", "斌", "波", "辉", "刚", "健", "明", "俊", "帆", "宇", "浩", "凯", "晨", "子轩", "浩然", "俊杰", "志强", "建国", "建华", "晓东", "文博", "天宇", "思远"]
FEMALE_GIVEN = ["芳", "娟", "敏", "静", "丽", "娜", "艳", "琳", "雪", "慧", "颖", "婷", "玉", "莹", "雪莲", "雨欣", "梦琪", "欣怡", "晓燕", "海燕", "佳怡", "思琪", "晓雯", "雅静", "诗涵"]
TERMINATE_REASONS_V = ["个人原因", "职业发展", "薪酬原因", "家庭原因", "健康原因", "深造学习"]
TERMINATE_REASONS_I = ["业绩不达标", "组织调整", "合同到期"]
TRAINING_COURSES = ["新员工入职培训", "信息安全与合规培训", "管理力提升工作坊", "跨部门沟通协作",
                    "技术分享会", "销售技巧实战营", "数据分析基础", "时间管理与效率提升", "领导力发展计划"]

DEPT_MULT = {d: mult for d, _, mult in DEPTS}
DEPT_ID = {dn: i for i, (dn, _, _) in enumerate(DEPTS, 1)}
dept_loc = {dn: loc for dn, loc, _ in DEPTS}
ID_DEPT = {i: dn for i, (dn, _, _) in enumerate(DEPTS, 1)}

def pick_name(seen, gender):
    while True:
        n = random.choice(SURNAME) + random.choice(MALE_GIVEN if gender == "男" else FEMALE_GIVEN)
        if len(n) >= 2 and n not in seen:
            seen.add(n)
            return n

def salary_for(level, dept, city):
    lo, hi = LEVEL_SALARY[level]
    v = random.uniform(lo, hi) * DEPT_MULT[dept] * CITY_MULT.get(city, 1.0) * 1000
    return float(max(5000, round(v / 100) * 100))

def tax_of(taxable):
    """简化月度个税"""
    t = max(0.0, taxable)
    for cap, rate in [(3000, .03), (12000, .10), (25000, .20), (35000, .25), (55000, .30), (80000, .35)]:
        if t <= cap:
            return round(t * rate, 2)
    return round(t * .45, 2)

# ---------------- 1. 部门 ----------------
departments = []
for i, (dn, loc, _) in enumerate(DEPTS, 1):
    departments.append({"dept_id": i, "dept_name": dn, "parent_id": None, "location": loc,
                        "established_date": dt.date(2020, 6, 1)})

# ---------------- 2. 员工 ----------------
employees = []          # dict
_seen_names = set()
def add_emp(hire_date, dept, title, level, city=None, etype=None, gender=None, age=None, salary=None, edu=None):
    eid = len(employees) + 1
    gender = gender or weighted(GENDER_W)
    if age is None:
        if level in ("总监", "副总裁"):
            age = random.randint(33, 48)
        else:
            age = weighted([(random.randint(22, 28), 45), (random.randint(29, 35), 35), (random.randint(36, 45), 20)])
    city = city or weighted([(dept_loc[dept], 60), (random.choice(CITIES), 40)])
    etype = etype or weighted([("全职", 82), ("外包", 8), ("实习", 6), ("兼职", 4)])
    emp = {
        "emp_id": eid, "emp_no": f"EMP{eid:04d}", "name": pick_name(_seen_names, gender),
        "gender": gender, "birth_date": dt.date(hire_date.year - age, hire_date.month, hire_date.day),
        "hire_date": hire_date, "dept_id": DEPT_ID[dept], "job_title": title, "job_level": level,
        "employment_type": etype, "status": "在职",
        "base_salary": salary if salary is not None else salary_for(level, dept, city),
        "manager_id": None, "work_city": city,
        "email": f"emp{eid:04d}@xingchen-tech.com",
        "phone": "1" + random.choice("3589") + "".join(random.choices("0123456789", k=8)),
        "education": edu or weighted(
            [("大专", 12), ("本科", 62), ("硕士", 22), ("博士", 4)] if level == "初级" else
            [("大专", 8), ("本科", 60), ("硕士", 28), ("博士", 4)] if level == "中级" else
            [("本科", 50), ("硕士", 40), ("博士", 10)] if level in ("高级", "专家") else
            [("本科", 25), ("硕士", 60), ("博士", 15)]),
        "termination_date": None, "termination_reason": None, "is_voluntary": None,
    }
    employees.append(emp)
    return emp

# CEO
ceo = add_emp(dt.date(2020, 6, 1), "总经办", "总经理", "副总裁", gender="男", age=42, etype="全职", edu="硕士")
# 各部门总监 (技术部配 2 名)
DIRECTORS = {}
for dn, _, _ in DEPTS:
    if dn == "总经办":
        continue
    n = 2 if dn == "技术部" else 1
    ds = []
    for j in range(n):
        title = "技术总监" if dn == "技术部" else f"{dn.replace('部', '')}总监"
        e = add_emp(dt.date(2020, 6, 1) + dt.timedelta(days=random.randint(0, 300)), dn, title, "总监",
                    etype="全职")
        e["manager_id"] = ceo["emp_id"]
        ds.append(e)
    DIRECTORS[dn] = ds

def dept_of(e):
    return ID_DEPT[e["dept_id"]]

# 2020-06 ~ 2022-12 初创员工 ~280 人
for _ in range(272):
    dn = weighted([(d, w) for d, w in DEPT_W.items() if w > 0])
    level = weighted([("初级", 40), ("中级", 35), ("高级", 18), ("专家", 7)])
    title = random.choice(TITLES[dn])
    hire = rand_workday(dt.date(2020, 6, 1), dt.date(2022, 12, 31))
    e = add_emp(hire, dn, title, level)
    e["manager_id"] = random.choice(DIRECTORS[dn])["emp_id"]

# ---------------- 3. 招聘岗位 (2023-01 ~ 2026-08 每月随机开放) ----------------
job_openings = []
def add_opening(opened, dept=None, title=None, level=None):
    dept = dept or weighted([(d, w) for d, w in DEPT_W.items() if w > 0])
    title = title or random.choice(TITLES[dept])
    level = level or weighted([("初级", 45), ("中级", 32), ("高级", 17), ("专家", 6)])
    lo, hi = LEVEL_SALARY[level]
    mult = DEPT_MULT[dept] * CITY_MULT[dept_loc[dept]]
    oid = len(job_openings) + 1
    job_openings.append({
        "opening_id": oid, "dept_id": DEPT_ID[dept], "job_title": title, "job_level": level,
        "headcount": random.randint(1, 3),
        "salary_min": round(lo * 1000 * mult / 100) * 100, "salary_max": round(hi * 1000 * mult / 100) * 100,
        "status": "招聘中", "opened_at": opened, "closed_at": None, "hired_count": 0, "_dept": dept,
    })
    return job_openings[-1]

for (yy, mm) in MONTHS:
    for _ in range(weighted([(0, 35), (1, 35), (2, 22), (3, 8)])):
        add_opening(month_rand_workday(yy, mm))

# ---------------- 4. 月度入职 (金三银四/金九银十, 候选人闭环) ----------------
candidates, _cand_names = [], set()
hires_plan = []
for (yy, mm) in MONTHS:
    season = {3: 2.0, 4: 1.7, 9: 1.8, 10: 1.5, 2: 0.4, 1: 0.7}.get(mm, 1.0)
    growth = 1.0 + 0.05 * (yy - 2023)
    n = max(0, min(24, int(round(random.uniform(7, 11) * season * growth))))
    hires_plan.extend([(yy, mm)] * n)

def month_of_open_opening(mdate):
    usable = [o for o in job_openings
              if o["opened_at"] <= mdate - dt.timedelta(days=14)
              and (o["closed_at"] is None or o["closed_at"] >= mdate)
              and o["hired_count"] < o["headcount"]]
    return random.choice(usable) if usable else None

for (yy, mm) in hires_plan:
    mdate = dt.date(yy, mm, 1)
    o = month_of_open_opening(mdate)
    if o is None:
        o = add_opening(mdate - dt.timedelta(days=random.randint(20, 40)))
        o["opened_at"] = mdate - dt.timedelta(days=random.randint(20, 40))
    hire_date = month_rand_workday(yy, mm)
    lo, hi = o["salary_min"], o["salary_max"]
    sal = round(random.uniform(float(lo) * 0.92, float(hi) * 1.08) / 100) * 100
    emp = add_emp(hire_date, o["_dept"], o["job_title"], o["job_level"],
                  city=None, etype=weighted([("全职", 88), ("外包", 6), ("实习", 4), ("兼职", 2)]),
                  salary=float(sal))
    emp["manager_id"] = random.choice(DIRECTORS[o["_dept"]])["emp_id"]
    o["hired_count"] += 1
    # 对应候选人(已入职)
    gender = weighted(GENDER_W)
    candidates.append({
        "cand_id": len(candidates) + 1, "name": pick_name(_cand_names, gender), "gender": gender,
        "opening_id": o["opening_id"],
        "source": weighted(SOURCE_W),
        "stage": "已入职",
        "applied_at": hire_date - dt.timedelta(days=random.randint(20, 45)),
        "expected_salary": round(random.uniform(float(lo), float(hi)) / 100) * 100,
        "hired_emp_id": emp["emp_id"],
    })

# 剩余岗位: 已关闭/仍招聘中 + 未录用的候选人 (所有岗位都会吸引落选候选人)
for o in job_openings:
    if o["hired_count"] >= o["headcount"]:
        o["status"] = "已关闭"
        o["closed_at"] = min(o["opened_at"] + dt.timedelta(days=random.randint(30, 150)), REF - dt.timedelta(days=1))
    elif o["opened_at"] >= REF - dt.timedelta(days=90) and random.random() < 0.7:
        o["status"] = "招聘中"
    else:
        o["status"] = "已关闭"
        o["closed_at"] = min(o["opened_at"] + dt.timedelta(days=random.randint(30, 150)), REF - dt.timedelta(days=1))
    for _ in range(random.randint(2, 6)):
        gender = weighted(GENDER_W)
        applied = o["opened_at"] + dt.timedelta(days=random.randint(0, 25))
        if applied >= REF:
            continue
        recent = (REF - applied).days <= 55
        stage = weighted([("已淘汰", 70), ("拒绝offer", 6),
                          ("已发offer", 6 if recent else 0), ("面试中", 18 if recent else 0)])
        candidates.append({
            "cand_id": len(candidates) + 1, "name": pick_name(_cand_names, gender), "gender": gender,
            "opening_id": o["opening_id"],
            "source": weighted(SOURCE_W),
            "stage": stage, "applied_at": applied,
            "expected_salary": round(random.uniform(float(o["salary_min"]), float(o["salary_max"])) / 100) * 100,
            "hired_emp_id": None,
        })

# ---------------- 5. 离职模拟 (2023-01 ~ 2026-08, 逐月) ----------------
def active_in_month(e, yy, mm):
    ms = dt.date(yy, mm, 1)
    me = dt.date(yy, mm, month_days(yy, mm))
    return e["hire_date"] <= me and (e["termination_date"] is None or e["termination_date"] >= ms)

INVOLUNTARY = list(zip(TERMINATE_REASONS_I, (45, 35, 20)))
VOLUNTARY = list(zip(TERMINATE_REASONS_V, (30, 25, 18, 12, 8, 7)))

for (yy, mm) in MONTHS:
    for e in employees:
        if e["status"] != "在职" or not active_in_month(e, yy, mm):
            continue
        if e["emp_id"] == ceo["emp_id"]:
            continue
        tenure = (dt.date(yy, mm, 1) - e["hire_date"]).days / 365.25
        if tenure <= 0:
            continue
        hz = 0.006
        if e["employment_type"] == "实习":
            hz = 0.09 if tenure > 0.25 else 0.0        # 实习期结束离开
        elif e["employment_type"] in ("外包", "兼职"):
            hz *= 2.2
        if tenure < 1:
            hz *= 2.2
        elif tenure < 2:
            hz *= 1.5
        if e["job_level"] == "初级":
            hz *= 1.4
        if e["job_level"] in ("总监", "副总裁"):
            hz *= 0.2
        if dept_of(e) in ("销售部", "客服部"):
            hz *= 1.5
        if random.random() < hz:
            td = month_rand_workday(yy, mm)
            if e["employment_type"] == "实习":
                reason, vol = "实习结束", True
            else:
                reason, vol = ((weighted(INVOLUNTARY), False) if random.random() < 0.16
                               else (weighted(VOLUNTARY), True))
            e["status"] = "离职"
            e["termination_date"] = td
            e["termination_reason"] = reason
            e["is_voluntary"] = vol

# ---------------- 6. 月度薪资发放 ----------------
def eff_base(e, month_first):
    f = 1.03 ** (-max(0.0, (dt.date(2026, 9, 1) - month_first).days / 365.25))
    return max(4000, round(e["base_salary"] * f / 100) * 100)

salary_payments = []
for (yy, mm) in MONTHS:
    mf = dt.date(yy, mm, 1)
    me = dt.date(yy, mm, month_days(yy, mm))
    nxt_y, nxt_m = (yy, mm + 1) if mm < 12 else (yy + 1, 1)
    pay_day = (dt.date(2026, 9, 10) if (nxt_y, nxt_m) > (2026, 8)
               else dt.date(nxt_y, nxt_m, 10))
    for e in employees:
        if e["hire_date"] > me:
            continue
        if e["termination_date"] is not None and e["termination_date"] < mf:
            continue
        base = eff_base(e, mf)
        ot = round(random.uniform(300, 2500), 2) if random.random() < (0.30 if dept_of(e) in ("技术部", "运营部", "客服部") else 0.05) else 0.0
        bonus = 0.0
        if mm == 1:  # 年终奖: 上一年在职满 6 个月
            prior_end = dt.date(yy - 1, 12, 31)
            worked = (min(e["termination_date"] or prior_end, prior_end) - max(e["hire_date"], dt.date(yy - 1, 1, 1))).days / 365.25
            if worked >= 0.5:
                bonus = round(base * random.uniform(0.8, 2.2), 2)
        elif random.random() < 0.08 and dept_of(e) in ("技术部", "产品部"):
            bonus = round(random.uniform(500, 4000), 2)
        si = round(base * 0.105, 2)
        tax = tax_of(base + ot + bonus - si - 5000)
        net = round(base + ot + bonus - si - tax, 2)
        salary_payments.append({
            "emp_id": e["emp_id"], "pay_period": f"{yy:04d}-{mm:02d}",
            "base_pay": float(base), "overtime_pay": ot, "bonus": bonus,
            "social_insurance": si, "income_tax": tax, "net_pay": net, "pay_date": pay_day,
        })

# ---------------- 7. 绩效考核 (2023H1 ~ 2026H1) ----------------
PERIODS = [(yy, h) for yy in (2023, 2024, 2025, 2026) for h in (1, 2) if (yy, h) != (2026, 2)]
PERIOD_END = {(yy, h): (dt.date(yy, 6, 30) if h == 1 else dt.date(yy, 12, 31)) for yy, h in PERIODS}
SENIORS = [e["emp_id"] for e in employees if e["job_level"] in ("高级", "专家", "总监", "副总裁")]
GRADE_W_DEFAULT = [("S", 10), ("A", 25), ("B", 45), ("C", 15), ("D", 5)]
GRADE_W_LEAVING = [("S", 4), ("A", 15), ("B", 40), ("C", 28), ("D", 13)]
GRADE_W_FLEX = [("S", 3), ("A", 15), ("B", 42), ("C", 28), ("D", 12)]
SCORE_RANGE = {"S": (88, 97), "A": (78, 87), "B": (65, 77), "C": (50, 64), "D": (30, 49)}
reviews = []
rid = 0
for (yy, h) in PERIODS:
    end = PERIOD_END[(yy, h)]
    for e in employees:
        if e["hire_date"] > end - dt.timedelta(days=60):
            continue
        if e["termination_date"] is not None and e["termination_date"] < end - dt.timedelta(days=30):
            continue
        w = (GRADE_W_FLEX if e["employment_type"] in ("外包", "实习")
             else GRADE_W_LEAVING if e["termination_date"] is not None and (e["termination_date"] - end).days < 365
             else GRADE_W_DEFAULT)
        g = weighted(w)
        score = round(random.uniform(*SCORE_RANGE[g]), 1)
        rid += 1
        reviews.append({"review_id": rid, "emp_id": e["emp_id"], "review_period": f"{yy}H{h}",
                        "score": score, "grade": g, "reviewer_id": e["manager_id"],
                        "comment": random.choice(["持续超出预期，业务结果突出", "目标达成良好，团队协作佳",
                                                  "符合岗位要求，表现稳定", "部分目标未达成，需改进",
                                                  "明显低于预期，已沟通改进计划", "成长速度快，可承担更大职责"])})

# ---------------- 8. 请假单 ----------------
LEAVE_DAYS = {"年假": [0.5, 1, 1, 2, 2, 3, 5], "病假": [0.5, 1, 1, 2, 3],
              "婚假": [3, 5, 10], "产假": [98, 128, 158]}
leaves = []
lid = 0
for e in employees:
    start_y = max(2023, e["hire_date"].year)
    end_y = e["termination_date"].year if e["termination_date"] else 2026
    for yy in range(start_y, end_y + 1):
        for _ in range(weighted([(0, 22), (1, 30), (2, 26), (3, 14), (4, 8)])):
            age = yy - e["birth_date"].year
            lt = weighted([("年假", 38), ("病假", 18), ("事假", 12), ("调休", 18),
                           ("婚假", 3), ("产假", 7 if e["gender"] == "女" and 24 <= age <= 38 else 0),
                           ("陪产假", 4 if e["gender"] == "男" and 25 <= age <= 40 else 0)])
            days = 15 if lt == "陪产假" else random.choice(LEAVE_DAYS.get(lt, [0.5, 1, 1, 2]))
            d0 = dt.date(yy, random.randint(1, 12), 1)
            sd = rand_workday(d0, d0 + dt.timedelta(days=27))
            ed = sd + dt.timedelta(days=max(0, int(days) - 1))
            if sd < e["hire_date"] or (e["termination_date"] and sd > e["termination_date"]) or sd >= REF:
                continue
            recent = (REF - sd).days <= 30
            st = weighted([("已批准", 80), ("待审批", 12 if recent else 0), ("已拒绝", 8)])
            lid += 1
            leaves.append({"leave_id": lid, "emp_id": e["emp_id"], "leave_type": lt,
                           "start_date": sd, "end_date": ed, "days": days, "status": st,
                           "approver_id": e["manager_id"],
                           "applied_at": f"{sd - dt.timedelta(days=random.randint(1, 14))} 10:{random.randint(10, 55)}:00"})

# ---------------- 9. 面试记录 ----------------
interviews = []
iid = 0
ROUNDS = ["一面", "二面", "三面", "HR面"]
for c in candidates:
    if c["stage"] == "简历筛选":
        continue
    n = {"已淘汰": random.randint(1, 2), "面试中": random.randint(1, 2), "已发offer": random.randint(2, 3),
         "已入职": random.randint(2, 4), "拒绝offer": random.randint(2, 3)}[c["stage"]]
    d = c["applied_at"] + dt.timedelta(days=random.randint(3, 10))
    for i in range(n):
        if d >= REF:
            break
        if c["stage"] == "已淘汰":
            result = "未通过" if i == n - 1 else random.choice(["通过", "未通过"])
            score = round(random.uniform(35, 70) if result == "未通过" else random.uniform(60, 85), 1)
        elif c["stage"] == "面试中":
            result = "待定" if i == n - 1 else "通过"
            score = round(random.uniform(60, 88), 1)
        else:
            result = "通过"
            score = round(random.uniform(68, 95), 1)
        iid += 1
        interviews.append({"interview_id": iid, "cand_id": c["cand_id"], "round": ROUNDS[i],
                           "interviewer_id": random.choice(SENIORS) if i < n - 1 else random.choice(
                               [e["emp_id"] for e in employees if e["dept_id"] == DEPT_ID["人事部"]]),
                           "interview_date": d, "score": score, "result": result})
        d += dt.timedelta(days=random.randint(4, 12))

# ---------------- 10. 培训记录 ----------------
trainings = []
tid = 0
for e in employees:
    for _ in range(random.randint(2, 6)):
        d = rand_workday(max(e["hire_date"], dt.date(2022, 6, 1)), (e["termination_date"] or REF) - dt.timedelta(days=1))
        if d >= REF:
            continue
        tid += 1
        trainings.append({"training_id": tid, "emp_id": e["emp_id"],
                          "course_name": random.choice(TRAINING_COURSES), "training_date": d,
                          "hours": random.choice([2, 3, 4, 6, 8]),
                          "completed": random.random() < 0.88,
                          "score": round(random.uniform(60, 99), 1) if random.random() < 0.8 else None})

# ---------------- 11. 调动记录 ----------------
transfers = []
trid = 0
for e in employees:
    if random.random() > 0.08:
        continue
    for _ in range(weighted([(1, 80), (2, 20)])):
        d = rand_workday(max(e["hire_date"] + dt.timedelta(days=180), dt.date(2023, 1, 1)),
                         (e["termination_date"] or REF) - dt.timedelta(days=1))
        if d >= REF:
            continue
        to_d = weighted([(x, w) for x, w in DEPT_W.items() if w > 0 and DEPT_ID[x] != e["dept_id"]])
        trid += 1
        transfers.append({"transfer_id": trid, "emp_id": e["emp_id"], "from_dept_id": e["dept_id"],
                          "to_dept_id": DEPT_ID[to_d], "transfer_date": d,
                          "reason": weighted([("组织调整", 40), ("个人发展", 40), ("晋升", 20)])})
        e["dept_id"] = DEPT_ID[to_d]  # 最近一次调动后部门

# ---------------- 写 CSV ----------------
TABLES = [
    ("departments.csv", ["dept_id", "dept_name", "parent_id", "location", "established_date"], departments),
    ("employees.csv", ["emp_id", "emp_no", "name", "gender", "birth_date", "hire_date", "dept_id",
                       "job_title", "job_level", "employment_type", "status", "base_salary", "manager_id",
                       "work_city", "email", "phone", "education", "termination_date", "termination_reason",
                       "is_voluntary"], employees),
    ("salary_payments.csv", ["emp_id", "pay_period", "base_pay", "overtime_pay", "bonus", "social_insurance",
                             "income_tax", "net_pay", "pay_date"], salary_payments),
    ("leave_requests.csv", ["emp_id", "leave_type", "start_date", "end_date", "days", "status",
                            "approver_id", "applied_at"], leaves),
    ("job_openings.csv", ["dept_id", "job_title", "job_level", "headcount", "salary_min", "salary_max",
                          "status", "opened_at", "closed_at", "hired_count"], job_openings),
    ("candidates.csv", ["name", "gender", "opening_id", "source", "stage", "applied_at",
                        "expected_salary", "hired_emp_id"], candidates),
    ("interviews.csv", ["cand_id", "round", "interviewer_id", "interview_date", "score", "result"], interviews),
    ("performance_reviews.csv", ["emp_id", "review_period", "score", "grade", "reviewer_id", "comment"], reviews),
    ("training_records.csv", ["emp_id", "course_name", "training_date", "hours", "completed", "score"], trainings),
    ("transfers.csv", ["emp_id", "from_dept_id", "to_dept_id", "transfer_date", "reason"], transfers),
]
for name, header, rows in TABLES:
    write_csv(OUT, name, header, rows)

# ---------------- 摘要 ----------------
active = [e for e in employees if e["status"] == "在职"]
terminated = [e for e in employees if e["status"] == "离职"]
for name, _, rows in TABLES:
    print(f"{name[:-4]:<18} {len(rows)}")
print(f"{'employees':<18} {len(employees)}  (在职 {len(active)} / 离职 {len(terminated)})")
