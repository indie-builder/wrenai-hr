#!/usr/bin/env python3
"""Deterministic baseline HR simulation. Stage order is part of the seed contract."""
from pathlib import Path

from seed_activities import leaves, training, transfers
from seed_common import SeedRandom, write_tables
from seed_payroll import payroll, reviews, terminate
from seed_people import People
from seed_recruitment import interviews, recruit


def generate():
    people = People(SeedRandom(42))
    hiring = recruit(people)
    terminate(people)
    salary = payroll(people)
    performance = reviews(people)
    leave = leaves(people)
    interview = interviews(people, hiring["candidates"])
    courses = training(people)
    transfer = transfers(people)
    return {"departments": people.departments, "employees": people.employees,
            "salary_payments": salary, "leave_requests": leave, **hiring, "interviews": interview,
            "performance_reviews": performance, "training_records": courses, "transfers": transfer}


if __name__ == "__main__":
    output = Path(__file__).resolve().parent / "out"
    output.mkdir(exist_ok=True)
    tables = generate()
    write_tables(output, **tables)
    employees = tables["employees"]
    active = sum(employee["status"] == "在职" for employee in employees)
    print(f"{'employees':<18} {len(employees)}  (在职 {active} / 离职 {len(employees) - active})")
