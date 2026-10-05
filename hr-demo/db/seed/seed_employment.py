"""Extension employment planning, promotions, contracts and salary changes."""
import datetime as dt

from seed_common import LEVELS, REF, active_at, base_at


def headcount(source):
    rng, rows = source.rng, []
    for year in (2023, 2024, 2025, 2026):
        end = dt.date(year, 12, 31)
        for dept in source.departments:
            actual = sum(employee["dept_id"] == dept["dept_id"] and active_at(employee, end) for employee in source.employees)
            planned = max(1, round(actual * rng.uniform(0.88, 1.15)))
            salary = rng.uniform(15000, 24000)
            rows.append({"plan_year": year, "dept_id": dept["dept_id"], "planned_headcount": planned,
                         "budget_labor_cost": float(round(planned * salary * 12 * 1.35 / 10000) * 10000),
                         "approved_at": dt.date(year - 1, 11, rng.randint(1, 28))})
    return rows


def promotions(source):
    rng, rows, promoted = source.rng, [], {}
    for year in (2023, 2024, 2025, 2026):
        pool = []
        for employee in source.active + source.terminated:
            window = dt.date(year, 3, 31)
            if employee["job_level"] not in ("初级", "中级", "高级") or (window - employee["hire_date"]).days < 550:
                continue
            if employee["termination_date"] and employee["termination_date"] < window:
                continue
            grade = source.grade(employee["emp_id"], f"{year - 1}H2") or source.grade(employee["emp_id"], f"{year - 1}H1")
            pool.append((employee, {"S": 40, "A": 22, "B": 8, "C": 3, "D": 1}.get(grade, 6)))
        if not pool:
            continue
        count, chosen = max(1, int(len(pool) * 0.07)), set()
        while len(chosen) < count:
            employee = rng.weighted(pool)
            if employee["emp_id"] in chosen:
                continue
            chosen.add(employee["emp_id"])
            day = rng.month_workday(year, 3)
            before = base_at(employee, day)
            after = round(before * rng.uniform(1.18, 1.32) / 100) * 100
            rows.append({"emp_id": employee["emp_id"], "promo_date": day,
                         "from_level": employee["job_level"], "to_level": LEVELS[LEVELS.index(employee["job_level"]) + 1],
                         "from_title": employee["job_title"],
                         "to_title": "高级" + employee["job_title"].replace("高级", "")
                         if employee["job_level"] == "中级" else employee["job_title"],
                         "salary_before": float(before), "salary_after": float(after),
                         "reason": rng.weighted([("年度晋升", 75), ("破格晋升", 15), ("继任就任", 10)])})
            promoted[employee["emp_id"]] = day
    return rows, promoted


def contracts(source):
    rng, rows = source.rng, []
    for employee in source.employees:
        hire = employee["hire_date"]
        short = {"实习": ("实习协议", 182), "外包": ("劳务协议", 365)}.get(employee["employment_type"])
        if short:
            periods = [(short[0], hire, hire + dt.timedelta(days=short[1]))]
        else:
            end = hire + dt.timedelta(days=3 * 365)
            periods = [("固定期限", hire, end)]
            if ((employee["termination_date"] or REF) - hire).days > 3 * 365 + 180:
                indefinite = employee["job_level"] in ("专家", "总监", "副总裁") or rng.random() < 0.12
                periods.append(("无固定期限" if indefinite else "固定期限", end + dt.timedelta(days=1),
                                None if indefinite else end + dt.timedelta(days=5 * 365)))
        for renewal, (kind, start, end) in enumerate(periods):
            if employee["termination_date"] and start <= employee["termination_date"]:
                status = "已解除"
                end = min(end, employee["termination_date"]) if end else employee["termination_date"]
            elif end and end < REF:
                status = "已到期"
            else:
                status = "履行中"
                if end and end <= REF + dt.timedelta(days=100) and rng.random() < 0.5:
                    end = REF + dt.timedelta(days=rng.randint(15, 88))
            rows.append({"contract_no": f"HT{len(rows) + 1:05d}", "emp_id": employee["emp_id"], "contract_type": kind,
                         "start_date": start, "end_date": end, "renewals": renewal, "status": status,
                         "signed_date": start - dt.timedelta(days=rng.randint(0, 15))})
    return rows


def salary_changes(source, promotion_rows):
    rng, rows = source.rng, []

    def append(emp_id, day, before, after, kind):
        rows.append({"emp_id": emp_id, "effective_date": day, "salary_before": float(before),
                     "salary_after": float(after), "change_pct": round((after / before - 1) * 100, 2), "change_type": kind})

    for year in (2023, 2024, 2025, 2026):
        day = rng.month_workday(year, 4)
        for employee in source.employees:
            if active_at(employee, day) and (day - employee["hire_date"]).days >= 300 and rng.random() < 0.65:
                before = base_at(employee, day)
                append(employee["emp_id"], day, before, round(before * rng.uniform(1.02, 1.08) / 100) * 100, "年度调薪")
    for promotion in promotion_rows:
        append(promotion["emp_id"], promotion["promo_date"], promotion["salary_before"], promotion["salary_after"], "晋升调薪")
    for employee in source.employees:
        if employee["status"] != "在职" or rng.random() > 0.02:
            continue
        day = rng.workday(dt.date(2024, 1, 1), dt.date(2026, 7, 31))
        before = base_at(employee, day)
        append(employee["emp_id"], day, before, round(before * rng.uniform(1.05, 1.15) / 100) * 100, "特批调薪")
    return rows
