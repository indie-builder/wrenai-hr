"""Performance goals, talent nominations, engagement and exit interviews."""
import datetime as dt

from seed_common import active_at


def goals(source):
    rng, rows = source.rng, []
    templates = {
        1: ["核心模块交付及时率≥95%", "线上故障数同比下降20%", "完成关键技术方案设计并通过评审", "带教1名新成员通过转正"],
        4: ["季度销售目标达成率≥90%", "新客户开发≥5家", "老客户续约率≥85%", "回款及时率≥95%"],
    }
    generic = ["季度关键任务按期交付", "跨部门协作满意度≥4分", "输出流程优化提案≥1项", "完成专业分享≥2次", "关键差错率为0"]
    for period in ("2024H1", "2024H2", "2025H1", "2025H2", "2026H1"):
        year = int(period[:4])
        end = dt.date(year, 6, 30) if period.endswith("H1") else dt.date(year, 12, 31)
        for employee in source.employees:
            if not active_at(employee, end - dt.timedelta(days=30)) or (end - employee["hire_date"]).days < 60:
                continue
            grade = source.grade(employee["emp_id"], period)
            completion = {"S": 88, "A": 78, "B": 65, "C": 50, "D": 35}.get(grade, 68)
            count = rng.randint(2, 4)
            weights = [10] * count
            for _ in range((100 - 10 * count) // 10):
                weights[rng.randrange(count)] += 10
            rng.shuffle(weights)
            template = templates.get(employee["dept_id"], generic)
            picks = rng.sample(template, min(count, len(template)))
            while len(picks) < count:
                picks.append(rng.choice(generic) + f"({rng.randint(2,9)})")
            for index in range(count):
                rows.append({"emp_id": employee["emp_id"], "review_period": period,
                             "goal_type": rng.weighted([("KPI", 70), ("OKR", 30)]),
                             "goal_desc": picks[index], "weight": weights[index],
                             "completion_pct": round(max(15, min(100, rng.gauss(completion, 12))), 1)})
    return rows


def talent(source, promoted):
    rng, rows = source.rng, []
    for employee in source.active:
        if employee["job_level"] not in ("初级", "中级", "高级") or rng.random() > 0.09:
            continue
        recent = [review for review in source.review_by_emp.get(employee["emp_id"], [])
                  if review["review_period"] in ("2025H2", "2026H1")]
        if not recent or not any(review["grade"] in ("S", "A") for review in recent):
            continue
        successor = rng.random() < 0.3
        dept = source.dept_names[employee["dept_id"]]
        rows.append({"emp_id": employee["emp_id"], "pool_type": "继任者" if successor else "高潜人才",
                     "target_position": f"{dept.replace('部', '')}总监" if successor else None,
                     "potential_rating": rng.weighted([("高潜", 60), ("潜力之星", 40)]),
                     "nominated_date": rng.workday(dt.date(2024, 6, 1), dt.date(2026, 6, 30)),
                     "nominated_by": source.director(employee["dept_id"]) or "", "status": "已晋升"
                     if employee["emp_id"] in promoted else rng.weighted([("在池", 92), ("已移出", 8)])})
    return rows[:60]


def surveys(source):
    rng, rows = source.rng, []
    for year in (2023, 2024, 2025, 2026):
        day = dt.date(year, 6, 1)
        for employee in source.active + source.terminated:
            if not active_at(employee, day) or rng.random() > 0.85:
                continue
            leaving = employee["termination_date"] and 0 <= (employee["termination_date"] - day).days <= 365
            base = rng.gauss(3.3, 0.45) - (0.5 if leaving else 0)
            dimensions = [max(1, min(5, rng.gauss(base + rng.uniform(-0.2, 0.2), 0.3))) for _ in range(5)]
            rows.append({"emp_id": employee["emp_id"], "survey_year": year,
                         "engagement_score": round(max(1, min(5, sum(dimensions) / 5)), 1),
                         **dict(zip(("recognition", "growth", "pay_satisfaction", "manager_trust", "work_life_balance"),
                                    (round(value, 1) for value in dimensions)))})
    return rows


def exits(source):
    rng, rows = source.rng, []
    for employee in source.terminated:
        if employee["termination_date"] < dt.date(2023, 2, 1) or rng.random() > 0.75:
            continue
        day = rng.workday(employee["termination_date"] - dt.timedelta(days=6), employee["termination_date"])
        reason = rng.weighted([("薪酬福利", 30), ("职业发展", 25), ("管理问题", 13), ("工作文化", 8), ("家庭个人", 16), ("健康", 8)])
        satisfaction = round(max(1, min(5, rng.gauss(2.9, 1.0))), 1)
        rows.append({"emp_id": employee["emp_id"], "interview_date": day, "real_reason_category": reason,
                     "satisfaction": satisfaction, "would_recommend": "t" if (satisfaction >= 3) == (rng.random() < 0.7) else "f",
                     "comment": rng.choice(["直属上级管理风格是主要考虑因素", "外部机会薪酬高出30%以上", "晋升通道不透明，看不到发展空间",
                                            "家庭原因需要回老家", "工作强度长期偏高", "认同公司文化，个人规划调整"])})
    return rows
