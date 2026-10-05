"""Typed baseline input and shared lookups for the extension simulation."""
import datetime as dt
from seed_common import SeedRandom, read_csv


class Extension:
    def __init__(self, directory):
        self.rng = SeedRandom(2026)
        self.employees = read_csv(directory, "employees.csv", emp_id=int, dept_id=int,
                                  hire_date=dt.date.fromisoformat, termination_date=dt.date.fromisoformat,
                                  base_salary=float)
        self.departments = read_csv(directory, "departments.csv", dept_id=int)
        self.reviews = read_csv(directory, "performance_reviews.csv", emp_id=int, score=float)
        self.candidates = read_csv(directory, "candidates.csv", opening_id=int, applied_at=dt.date.fromisoformat,
                                   expected_salary=float, hired_emp_id=int)
        for index, candidate in enumerate(self.candidates, 1):
            candidate["cand_id"] = index
        self.leaves = read_csv(directory, "leave_requests.csv", emp_id=int, days=float, start_date=dt.date.fromisoformat)
        self.salary_rows = read_csv(directory, "salary_payments.csv", emp_id=int, base_pay=float)
        self.active = [employee for employee in self.employees if employee["status"] == "在职"]
        self.terminated = [employee for employee in self.employees if employee["status"] == "离职"]
        self.by_id = {employee["emp_id"]: employee for employee in self.employees}
        self.directors = [employee for employee in self.employees if employee["job_level"] == "总监"]
        self.grades = {}
        self.review_by_emp = {}
        for review in self.reviews:
            self.grades.setdefault((review["emp_id"], review["review_period"]), review["grade"])
            self.review_by_emp.setdefault(review["emp_id"], []).append(review)
        self.dept_names = {dept["dept_id"]: dept["dept_name"] for dept in self.departments}

    def director(self, dept_id):
        directors = [employee for employee in self.directors if employee["dept_id"] == dept_id]
        return self.rng.choice(directors)["emp_id"] if directors else None

    def grade(self, emp_id, period):
        return self.grades.get((emp_id, period))
