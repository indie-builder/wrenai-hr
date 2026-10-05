"""Lifecycle, monthly pay and half-year reviews, executed in this random-stream order."""
import datetime as dt

from seed_common import MONTHS, REF, base_at, month_days


def terminate(people):
    rng = people.rng
    involuntary = [("业绩不达标", 45), ("组织调整", 35), ("合同到期", 20)]
    voluntary = [("个人原因", 30), ("职业发展", 25), ("薪酬原因", 18), ("家庭原因", 12), ("健康原因", 8), ("深造学习", 7)]
    for year, month in MONTHS:
        start = dt.date(year, month, 1)
        end = dt.date(year, month, month_days(year, month))
        for employee in people.employees:
            if employee["status"] != "在职" or employee["hire_date"] > end or employee is people.ceo:
                continue
            tenure = (start - employee["hire_date"]).days / 365.25
            if tenure <= 0:
                continue
            hazard = 0.006
            employment = employee["employment_type"]
            if employment == "实习":
                hazard = 0.09 if tenure > 0.25 else 0.0
            elif employment in ("外包", "兼职"):
                hazard *= 2.2
            hazard *= 2.2 if tenure < 1 else 1.5 if tenure < 2 else 1
            if employee["job_level"] == "初级":
                hazard *= 1.4
            if employee["job_level"] in ("总监", "副总裁"):
                hazard *= 0.2
            if people.department(employee) in ("销售部", "客服部"):
                hazard *= 1.5
            if rng.random() < hazard:
                day = rng.month_workday(year, month)
                if employment == "实习":
                    reason, is_voluntary = "实习结束", True
                else:
                    reason, is_voluntary = ((rng.weighted(involuntary), False) if rng.random() < 0.16
                                             else (rng.weighted(voluntary), True))
                employee.update(status="离职", termination_date=day, termination_reason=reason,
                                is_voluntary=is_voluntary)


def tax_of(taxable):
    taxable = max(0.0, taxable)
    for cap, rate in [(3000, .03), (12000, .10), (25000, .20), (35000, .25), (55000, .30), (80000, .35)]:
        if taxable <= cap:
            return round(taxable * rate, 2)
    return round(taxable * .45, 2)


def payroll(people):
    rng, rows = people.rng, []
    for year, month in MONTHS:
        first = dt.date(year, month, 1)
        end = dt.date(year, month, month_days(year, month))
        pay_day = dt.date(year + (month == 12), month % 12 + 1, 10)
        for employee in people.employees:
            if employee["hire_date"] > end or (employee["termination_date"] and employee["termination_date"] < first):
                continue
            base = base_at(employee, first)
            department = people.department(employee)
            overtime = (round(rng.uniform(300, 2500), 2)
                        if rng.random() < (0.30 if department in ("技术部", "运营部", "客服部") else 0.05) else 0.0)
            bonus = 0.0
            if month == 1:
                prior_end = dt.date(year - 1, 12, 31)
                worked = (min(employee["termination_date"] or prior_end, prior_end)
                          - max(employee["hire_date"], dt.date(year - 1, 1, 1))).days / 365.25
                if worked >= 0.5:
                    bonus = round(base * rng.uniform(0.8, 2.2), 2)
            elif rng.random() < 0.08 and department in ("技术部", "产品部"):
                bonus = round(rng.uniform(500, 4000), 2)
            insurance = round(base * 0.105, 2)
            tax = tax_of(base + overtime + bonus - insurance - 5000)
            rows.append({"emp_id": employee["emp_id"], "pay_period": f"{year:04d}-{month:02d}",
                         "base_pay": float(base), "overtime_pay": overtime, "bonus": bonus,
                         "social_insurance": insurance, "income_tax": tax,
                         "net_pay": round(base + overtime + bonus - insurance - tax, 2), "pay_date": pay_day})
    return rows


def reviews(people):
    rng, rows = people.rng, []
    default = [("S", 10), ("A", 25), ("B", 45), ("C", 15), ("D", 5)]
    leaving = [("S", 4), ("A", 15), ("B", 40), ("C", 28), ("D", 13)]
    flex = [("S", 3), ("A", 15), ("B", 42), ("C", 28), ("D", 12)]
    ranges = {"S": (88, 97), "A": (78, 87), "B": (65, 77), "C": (50, 64), "D": (30, 49)}
    comments = ["持续超出预期，业务结果突出", "目标达成良好，团队协作佳", "符合岗位要求，表现稳定",
                "部分目标未达成，需改进", "明显低于预期，已沟通改进计划", "成长速度快，可承担更大职责"]
    periods = [(year, half) for year in (2023, 2024, 2025, 2026) for half in (1, 2) if (year, half) != (2026, 2)]
    for year, half in periods:
        end = dt.date(year, 6, 30) if half == 1 else dt.date(year, 12, 31)
        for employee in people.employees:
            if employee["hire_date"] > end - dt.timedelta(days=60):
                continue
            if employee["termination_date"] and employee["termination_date"] < end - dt.timedelta(days=30):
                continue
            weights = (flex if employee["employment_type"] in ("外包", "实习") else leaving
                       if employee["termination_date"] and (employee["termination_date"] - end).days < 365 else default)
            grade = rng.weighted(weights)
            rows.append({"emp_id": employee["emp_id"], "review_period": f"{year}H{half}",
                         "score": round(rng.uniform(*ranges[grade]), 1), "grade": grade,
                         "reviewer_id": employee["manager_id"], "comment": rng.choice(comments)})
    return rows
