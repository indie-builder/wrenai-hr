"""Leave, training and transfers; interviews run between leave and training."""
import datetime as dt

from seed_common import REF
from seed_people import DEPT_W


def leaves(people):
    rng, rows = people.rng, []
    days_by_type = {"年假": [0.5, 1, 1, 2, 2, 3, 5], "病假": [0.5, 1, 1, 2, 3],
                    "婚假": [3, 5, 10], "产假": [98, 128, 158]}
    for employee in people.employees:
        start = max(2023, employee["hire_date"].year)
        end = employee["termination_date"].year if employee["termination_date"] else 2026
        for year in range(start, end + 1):
            for _ in range(rng.weighted([(0, 22), (1, 30), (2, 26), (3, 14), (4, 8)])):
                age = year - employee["birth_date"].year
                kind = rng.weighted([("年假", 38), ("病假", 18), ("事假", 12), ("调休", 18), ("婚假", 3),
                                     ("产假", 7 if employee["gender"] == "女" and 24 <= age <= 38 else 0),
                                     ("陪产假", 4 if employee["gender"] == "男" and 25 <= age <= 40 else 0)])
                days = 15 if kind == "陪产假" else rng.choice(days_by_type.get(kind, [0.5, 1, 1, 2]))
                first = dt.date(year, rng.randint(1, 12), 1)
                day = rng.workday(first, first + dt.timedelta(days=27))
                last = day + dt.timedelta(days=max(0, int(days) - 1))
                if (day < employee["hire_date"] or (employee["termination_date"] and day > employee["termination_date"])
                        or day >= REF):
                    continue
                status = rng.weighted([("已批准", 80), ("待审批", 12 if (REF - day).days <= 30 else 0), ("已拒绝", 8)])
                rows.append({"emp_id": employee["emp_id"], "leave_type": kind, "start_date": day, "end_date": last,
                             "days": days, "status": status, "approver_id": employee["manager_id"],
                             "applied_at": f"{day - dt.timedelta(days=rng.randint(1, 14))} 10:{rng.randint(10, 55)}:00"})
    return rows


def training(people):
    rng, rows = people.rng, []
    courses = ["新员工入职培训", "信息安全与合规培训", "管理力提升工作坊", "跨部门沟通协作", "技术分享会",
               "销售技巧实战营", "数据分析基础", "时间管理与效率提升", "领导力发展计划"]
    for employee in people.employees:
        for _ in range(rng.randint(2, 6)):
            day = rng.workday(max(employee["hire_date"], dt.date(2022, 6, 1)),
                              (employee["termination_date"] or REF) - dt.timedelta(days=1))
            if day >= REF:
                continue
            rows.append({"emp_id": employee["emp_id"], "course_name": rng.choice(courses), "training_date": day,
                         "hours": rng.choice([2, 3, 4, 6, 8]), "completed": rng.random() < 0.88,
                         "score": round(rng.uniform(60, 99), 1) if rng.random() < 0.8 else None})
    return rows


def transfers(people):
    rng, rows = people.rng, []
    for employee in people.employees:
        if rng.random() > 0.08:
            continue
        for _ in range(rng.weighted([(1, 80), (2, 20)])):
            day = rng.workday(max(employee["hire_date"] + dt.timedelta(days=180), dt.date(2023, 1, 1)),
                              (employee["termination_date"] or REF) - dt.timedelta(days=1))
            if day >= REF:
                continue
            dept = rng.weighted([(name, weight) for name, weight in DEPT_W if people.dept_id[name] != employee["dept_id"]])
            rows.append({"emp_id": employee["emp_id"], "from_dept_id": employee["dept_id"],
                         "to_dept_id": people.dept_id[dept], "transfer_date": day,
                         "reason": rng.weighted([("组织调整", 40), ("个人发展", 40), ("晋升", 20)])})
            employee["dept_id"] = people.dept_id[dept]
    return rows
