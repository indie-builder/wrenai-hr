"""Monthly hiring pipeline and interviews, sharing employee identities and RNG."""
import datetime as dt

from seed_common import MONTHS, REF
from seed_people import CITY_MULT, DEPT_W, GENDER_W, LEVEL_SALARY, SOURCE_W, TITLES


def recruit(people):
    rng = people.rng
    openings, candidates, seen = [], [], set()

    def opening(opened, dept=None, title=None, level=None):
        dept = dept or rng.weighted(DEPT_W)
        title = title or rng.choice(TITLES[dept])
        level = level or rng.weighted([("初级", 45), ("中级", 32), ("高级", 17), ("专家", 6)])
        low, high = LEVEL_SALARY[level]
        factor = people.multipliers[dept] * CITY_MULT[people.locations[dept]]
        row = {"opening_id": len(openings) + 1, "dept_id": people.dept_id[dept],
               "job_title": title, "job_level": level, "headcount": rng.randint(1, 3),
               "salary_min": round(low * 1000 * factor / 100) * 100,
               "salary_max": round(high * 1000 * factor / 100) * 100,
               "status": "招聘中", "opened_at": opened, "closed_at": None, "hired_count": 0, "_dept": dept}
        openings.append(row)
        return row

    def candidate(job, gender, stage, applied, salary, hired=None):
        # Salary draws occur after name/source draws in the original stream.
        row = {"cand_id": len(candidates) + 1, "name": people.name(seen, gender), "gender": gender,
               "opening_id": job["opening_id"], "source": rng.weighted(SOURCE_W),
               "stage": stage, "applied_at": applied(), "expected_salary": salary(), "hired_emp_id": hired}
        candidates.append(row)

    for year, month in MONTHS:
        for _ in range(rng.weighted([(0, 35), (1, 35), (2, 22), (3, 8)])):
            opening(rng.month_workday(year, month))
    plan = []
    for year, month in MONTHS:
        season = {3: 2.0, 4: 1.7, 9: 1.8, 10: 1.5, 2: 0.4, 1: 0.7}.get(month, 1.0)
        count = max(0, min(24, int(round(rng.uniform(7, 11) * season * (1.0 + 0.05 * (year - 2023))))))
        plan.extend([(year, month)] * count)
    for year, month in plan:
        first = dt.date(year, month, 1)
        usable = [job for job in openings if job["opened_at"] <= first - dt.timedelta(days=14)
                  and (job["closed_at"] is None or job["closed_at"] >= first)
                  and job["hired_count"] < job["headcount"]]
        job = rng.choice(usable) if usable else None
        if job is None:
            job = opening(first - dt.timedelta(days=rng.randint(20, 40)))
            job["opened_at"] = first - dt.timedelta(days=rng.randint(20, 40))
        hire = rng.month_workday(year, month)
        low, high = job["salary_min"], job["salary_max"]
        salary = round(rng.uniform(float(low) * 0.92, float(high) * 1.08) / 100) * 100
        employee = people.add(hire, job["_dept"], job["job_title"], job["job_level"],
                              etype=rng.weighted([("全职", 88), ("外包", 6), ("实习", 4), ("兼职", 2)]),
                              salary=float(salary))
        employee["manager_id"] = rng.choice(people.directors[job["_dept"]])["emp_id"]
        job["hired_count"] += 1
        gender = rng.weighted(GENDER_W)
        candidate(job, gender, "已入职", lambda: hire - dt.timedelta(days=rng.randint(20, 45)),
                  lambda: round(rng.uniform(float(low), float(high)) / 100) * 100, employee["emp_id"])
    for job in openings:
        full = job["hired_count"] >= job["headcount"]
        if full or not (job["opened_at"] >= REF - dt.timedelta(days=90) and rng.random() < 0.7):
            job["status"] = "已关闭"
            job["closed_at"] = min(job["opened_at"] + dt.timedelta(days=rng.randint(30, 150)), REF - dt.timedelta(days=1))
        for _ in range(rng.randint(2, 6)):
            gender = rng.weighted(GENDER_W)
            applied = job["opened_at"] + dt.timedelta(days=rng.randint(0, 25))
            if applied >= REF:
                continue
            recent = (REF - applied).days <= 55
            stage = rng.weighted([("已淘汰", 70), ("拒绝offer", 6),
                                  ("已发offer", 6 if recent else 0), ("面试中", 18 if recent else 0)])
            candidate(job, gender, stage, lambda: applied,
                      lambda: round(rng.uniform(float(job["salary_min"]), float(job["salary_max"])) / 100) * 100)
    return {"job_openings": openings, "candidates": candidates}


def interviews(people, candidates):
    rng = people.rng
    seniors = [employee["emp_id"] for employee in people.employees
               if employee["job_level"] in ("高级", "专家", "总监", "副总裁")]
    hr = [employee["emp_id"] for employee in people.employees if employee["dept_id"] == people.dept_id["人事部"]]
    rounds, rows = ["一面", "二面", "三面", "HR面"], []
    for candidate in candidates:
        stage = candidate["stage"]
        if stage == "简历筛选":
            continue
        # All five draws are intentional; moving the lookup before them changes the seed.
        count = {"已淘汰": rng.randint(1, 2), "面试中": rng.randint(1, 2), "已发offer": rng.randint(2, 3),
                 "已入职": rng.randint(2, 4), "拒绝offer": rng.randint(2, 3)}[stage]
        day = candidate["applied_at"] + dt.timedelta(days=rng.randint(3, 10))
        for index in range(count):
            if day >= REF:
                break
            if stage == "已淘汰":
                result = "未通过" if index == count - 1 else rng.choice(["通过", "未通过"])
                score = round(rng.uniform(35, 70) if result == "未通过" else rng.uniform(60, 85), 1)
            else:
                result = "待定" if stage == "面试中" and index == count - 1 else "通过"
                score = round(rng.uniform(60, 88) if stage == "面试中" else rng.uniform(68, 95), 1)
            rows.append({"cand_id": candidate["cand_id"], "round": rounds[index],
                         "interviewer_id": rng.choice(seniors if index < count - 1 else hr),
                         "interview_date": day, "score": score, "result": result})
            day += dt.timedelta(days=rng.randint(4, 12))
    return rows
