"""Employer insurance, recognition, overtime and leave balances."""
import datetime as dt

from seed_common import MONTHS, REF, active_at, month_days


def insurance(source):
    rates = [("pension", .16), ("medical", .095), ("unemployment", .005),
             ("injury", .004), ("maternity", .008), ("housing_fund", .08)]
    rows = []
    for salary in source.salary_rows:
        amounts = {name: round(salary["base_pay"] * rate, 2) for name, rate in rates}
        rows.append({"emp_id": salary["emp_id"], "pay_period": salary["pay_period"], **amounts,
                     "company_total": round(sum(amounts.values()), 2)})
    return rows


def awards(source):
    rng, rows = source.rng, []
    for review in source.reviews:
        employee = source.by_id[review["emp_id"]]
        year = int(review["review_period"][:4])
        if review["grade"] in ("S", "A") and rng.random() < 0.6:
            start = dt.date(year + 1, 1, 1)
            if start > REF:
                continue
            cash = review["grade"] == "S" or rng.random() < 0.5
            rows.append({"emp_id": employee["emp_id"], "record_date": rng.workday(start, dt.date(year + 1, 2, 28)),
                         "record_type": "奖励", "category": "奖金" if cash else rng.choice(["通报表扬", "优秀员工"]),
                         "amount": float(round(rng.uniform(1000, 5000), 2)) if cash else None,
                         "reason": f"{review['review_period']}绩效{review['grade']}级", "approver_id": employee["manager_id"] or ""})
        elif review["grade"] in ("C", "D") and rng.random() < 0.18:
            start = dt.date(year, 7, 1)
            if start > REF:
                continue
            rows.append({"emp_id": employee["emp_id"], "record_date": rng.workday(start, dt.date(year, 12, 31)),
                         "record_type": "处罚", "category": rng.choice(["警告", "记过"]), "amount": None,
                         "reason": rng.choice(["阶段性目标未达成", "考勤纪律问题", "工作失误造成返工"]),
                         "approver_id": employee["manager_id"] or ""})
    return rows


def overtime(source):
    rng, rows = source.rng, []
    for employee in source.employees:
        probability = 0.35 if employee["dept_id"] in (1, 3, 6) else 0.15
        for year, month in MONTHS[24:]:
            if not active_at(employee, dt.date(year, month, month_days(year, month))):
                continue
            if rng.random() < probability:
                day = rng.month_workday(year, month)
                if not active_at(employee, day):
                    continue
                hours = rng.choice([2, 3, 4, 4, 6, 8])
                status = rng.weighted([("已批准", 85), ("已拒绝", 10), ("待审批", 5)])
                rows.append({"emp_id": employee["emp_id"], "ot_date": day, "planned_hours": hours,
                             "actual_hours": hours if status != "已拒绝" else None,
                             "reason": rng.choice(["项目上线冲刺", "紧急故障处理", "月度结账", "客户交付", "版本发布", "季度盘点"]),
                             "status": status, "compensation": rng.weighted([("调休", 60), ("加班费", 35), ("无", 5)])
                             if status == "已批准" else "无", "applied_at": day - dt.timedelta(days=rng.randint(1, 5))})
    return rows


def leave_balances(source, overtime_rows):
    compensations = {}
    for overtime in overtime_rows:
        if overtime["compensation"] == "调休" and overtime["status"] == "已批准":
            emp_id = overtime["emp_id"]
            compensations[emp_id] = compensations.get(emp_id, 0.0) + overtime["planned_hours"] / 8.0
    rows = []
    for employee in source.employees:
        tenure = max(0, min(10, int(((employee["termination_date"] or REF) - employee["hire_date"]).days / 365.25)))
        for year in (2025, 2026):
            for quarter in (1, 2, 3):
                end = dt.date(year, quarter * 3, month_days(year, quarter * 3))
                if not active_at(employee, end):
                    continue
                for kind, entitled in (("年假", 5 + min(10, tenure)), ("调休", compensations.get(employee["emp_id"], 0.0))):
                    used = sum(leave["days"] for leave in source.leaves if leave["emp_id"] == employee["emp_id"]
                               and leave["leave_type"] == kind and leave["status"] == "已批准"
                               and leave["start_date"].year == year and leave["start_date"] <= end)
                    expired = max(0.0, entitled - used) if kind == "年假" and (year, quarter) == (2026, 1) else 0.0
                    rows.append({"emp_id": employee["emp_id"], "balance_type": kind, "as_of_quarter": f"{year}Q{quarter}",
                                 "entitled": float(entitled) if kind == "年假" else round(entitled, 1), "used": round(used, 1),
                                 "remaining": round(max(0.0, entitled - used), 1), "expired": round(expired, 1)})
    return rows
