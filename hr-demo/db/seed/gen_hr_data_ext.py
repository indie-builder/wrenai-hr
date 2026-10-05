#!/usr/bin/env python3
"""Deterministic extension simulation; reads baseline CSV without changing it."""
from pathlib import Path

from seed_benefits import awards, insurance, leave_balances, overtime
from seed_common import write_tables
from seed_development import exits, goals, surveys, talent
from seed_employment import contracts, headcount, promotions, salary_changes
from seed_extension import Extension
from seed_offers import costs, offers


def generate(directory):
    source = Extension(directory)
    plan = headcount(source)
    promoted, promotion_dates = promotions(source)
    contract = contracts(source)
    changes = salary_changes(source, promoted)
    benefits = insurance(source)
    recognition = awards(source)
    extra_hours = overtime(source)
    balances = leave_balances(source, extra_hours)
    offer = offers(source)
    expenses = costs(source)
    targets = goals(source)
    pool = talent(source, promotion_dates)
    engagement = surveys(source)
    interviews = exits(source)
    return {"headcount_plan": plan, "promotions": promoted, "contracts": contract, "salary_changes": changes,
            "insurance_payments": benefits, "awards_penalties": recognition, "overtime_requests": extra_hours,
            "leave_balances": balances, "offers": offer, "recruitment_costs": expenses,
            "performance_goals": targets, "talent_pool": pool, "engagement_surveys": engagement, "exit_interviews": interviews}


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    output = here / "out2"
    output.mkdir(exist_ok=True)
    write_tables(output, **generate(here / "out"))
